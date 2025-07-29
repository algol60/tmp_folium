#

# A script that modifies folium to work offline.
#
# Problem: We want to use folium off-line, ie no Internet available.
#
# Folium requires files that are typically downloaded from CDNs.
# This doesn't work if there is no Internet.
# Futhermore, referenced CSS files themselves contain references to
# Internet files using url(). These also have to be processed.
#
# Therefore, we look through the folium .py files for https URLs,
# download the referenced files (renaming them, see below),
# update the .py files with a local URL, and overwrite the .py files.
# For each downloaded .css file, look for url(...), determine the right thing to download
# (taking relative URLs into account), and update the .css file.
#
# Note that all downloaded files are written to files called (sha1 of URL).ext.
# This ensures that unique URLs produce unique filenames, and avoids mucking
# about with directory trees. The mapping from original URL to downloaded
# file is stored in zzdownloaded.json for reference.
#
# The script is run as a multi-stage process.
#
# While connected to the Internet:
# - git clone folium from github to D:/tmp/git (for example).
# - "git checkout tag"  the required version by looking at the tags ()"git tag".).
# - to modify the folium source code at D:\tmp\git\folium\folium and download the files to D:\tmp\cdn:
# $ python .\local_folium.py --folium D:\tmp\git\folium\folium --dir D:\tmp\cdn process
#
# This will modify the folium .py files and produce a directory full of files at D:/tmp/cdn.
#
# - copy the downloaded files off-line. Optionally copy the modified folium files.
#
# If there is a copy of the original folium files off-line, we just want to modify the files:
# $ python .\local_folium.py --folium D:\tmp\git\folium\folium process
#
# Replace the placeholder URL in the .py files and the downloaded files.
#
# $ python .\local_folium.py --folium D:\tmp\git\folium\folium --dir D:\tmp\cdn replace --url https://local_cdn/base
#
# - edit setup.py to change the wheel name; change name="...".
# - build the modified folium (requires setuptools, setuptools_scm, wheel)
# > $env:SETUPTOOLS_SCM_PRETEND_VERSION='0.20.0'
# > python -m build -w -n
# - publish the wheel.

import argparse
from hashlib import sha1
import json
from pathlib import Path
import re
import requests
import sys

# A unique placeholder string.
# In the first pass, the original URLs will be replaced with f'{PLACEHOLDER}/newname.ext'.
# In the second pass, the placeholder will be replaced with the new URL base path.
#
PLACEHOLDER = '__mystery://placeholder__'

# For our purposes, a URL is a quoted string containing https://...
# where ... are any non-quote characters.
#
URL_RE = URL_RE = re.compile(r'''['"](https://[^'"]+)['"]''', re.IGNORECASE)

# CSS file scontain references in url() function calls.
#
CSS_RE = re.compile(r'''url\(([^)]+)\)''')

def url_relative(u, r):
    """Given a URL u, produce a modified URL using relative path r."""

    s = u.rfind('/')
    u = u[:s]

    while r.startswith('../'):
        s = u.rfind('/')
        u = u[:s]
        r = r[3:]

    return f'{u}/{r}'

def url_to_name(u):
    """Convert a URL to a plain filename.

    Hash the URL and maintain the suffix.
    """

    if u.startswith('//'):
        # Relative protocol in CSS files.
        #
        u = f'https:{u}'

    if not u.startswith('https://'):
        raise ValueError('Convert a URL, not "{u}"')

    # "leaflet.css" is used to determine file locations.
    # "marker*.png" are hard-coded.
    # Don't rename these.
    #
    if u.endswith((
        'leaflet.css',
        'marker-icon.png',
        'marker-icon-2x.png',
        'marker-shadow.png'
        )):
        print(f'UNCHANGED: {u}')
        return u[u.rindex('/')+1:]

    name = sha1(u.encode('UTF-8')).hexdigest()

    suffix = Path(u).suffix
    if (ix:=suffix.find('#')>=0):
        suffix = suffix[:ix]

    return f'{name}{suffix}'

class UrlModifier:
    def __init__(self):
        # Map original URLS to local URLs.
        #
        self.url_map = {}

    def local_https(self, line):
        """If this line contains a URL, modify it to contain a local URL.

        The old URL and new name are recorded in self.downloads.
        """

        m = URL_RE.search(line)
        if not m:
            return line

        sline = line.strip()
        if sline.startswith(('>>>', '...')):
            # This looks like a docstring, so leave it alone.
            #
            return line

        print(line, end='')
        b, e = m.span()
        old_u = line[b+1:e-1]

        name = url_to_name(old_u)

        line = f'{line[:b+1]}{PLACEHOLDER}/{name}{line[e-1:]}'
        print(line)

        # If old_u has been seen before, it will already be in url_map.
        # This is fine - identical old_u values will map to identical hashes,
        # so just overwriting it is fine.
        #
        self.url_map[old_u] = name

        return line

    def process_py(self, folium_p):
        """Look for https URLs in strings.

        Assume one per line."""

        for p in folium_p.glob('**/*.py'):
            with p.open(encoding='UTF-8') as f:
                old_lines = f.readlines()
                upd_lines = [self.local_https(line) for line in old_lines]
                if upd_lines!=old_lines:
                    print(f'Folium: {p}')
                    with p.open('w', newline='', encoding='UTF_8') as f:
                        f.writelines(upd_lines)

    def download_from_css(self, local_dir):
        """Inspect downloaded CSS files, find url() function calls, and update those."""

        for old_u, name in dict(self.url_map).items():
            if name.endswith('.css'):
                print(f'CSS: {old_u}\n{name}')
                with (local_dir / name).open(encoding='UTF-8') as f:
                    text = ''.join(f.readlines())

                # Reverse the matches so the spans aren't altered.
                #
                matched = False
                for m in reversed(list(CSS_RE.finditer(text))):
                    # print(m)
                    b, e = m.span(1)
                    u2 = text[b:e]
                    if u2.startswith(('"', "'")):
                        b, e = b+1, e-1
                        u2 = text[b:e]

                    if (ix:=u2.find('?'))>=0:
                        # Remove IE8 fix.
                        #
                        u2 = u2[:ix]

                    if u2.startswith(('data:', '#', '%')):
                        # Inline data - don't do anything.
                        #
                        pass
                    else:
                        if u2.startswith('//'):
                            u2 = f'https:{u2}'
                        else:
                            u2 = url_relative(old_u, u2)

                        if u2 not in self.url_map:
                            name2 = url_to_name(u2)
                            download_file(u2, local_dir, name2)
                            self.url_map[u2] = name2

                            # If this is the marker icon, also fetch the other two marker icons.
                            #
                            if name2=='marker-icon.png':
                                for also_name in ['marker-icon-2x.png', 'marker-shadow.png']:
                                    also_u = u2.replace('marker-icon.png', also_name)
                                    print(f'ADD: {also_u}\n{also_name}')
                                    download_file(also_u, local_dir, also_name)
                                    self.url_map[also_u] = also_name

                        text = f'{text[:b]}{PLACEHOLDER}/{name2}{text[e:]}'

                        matched = True

                        print(u2)
                        print(name2)

                if matched:
                    with (local_dir / name).open('w', encoding='UTF-8') as f:
                        f.write(text)

                print()

    def download_from_py(self, local_dir):
        for old_u, name in self.url_map.items():
            print(f'DOWNLOAD PY: {old_u}\n{name}\n')

            download_file(old_u, local_dir, name)

        # self.download_from_css(local_dir)

        # with open(local_dir / 'zzdownloaded.json', 'w', encoding='UTF-8') as f:
        #     json.dump(self.url_map, f, indent=2)

def download_file(u, local_dir, name):
    r = requests.get(u)
    local_file = local_dir / name
    with local_file.open('wb') as f:
        f.write(r.content)

def _process(args):
    if not args.folium:
        print('Must specify the folium root directory.')
        sys.exit(1)

    folium_p = Path(args.folium)
    um = UrlModifier()
    um.process_py(folium_p)

    if args.dir:
        local_dir_p = Path(args.dir)

        um.download_from_py(local_dir_p)
        um.download_from_css(local_dir_p)

        with open(local_dir_p / 'zzdownloaded.json', 'w', encoding='UTF-8') as f:
            json.dump(um.url_map, f, indent=2)

def _replace(args):
    """Replace the placeholder in the specified files with the actual URL."""

    def replace(base, pattern, url_prefix):
        for p in base.glob(pattern):
            with p.open(encoding='UTF-8') as f:
                buf = f.read()

            if buf.find(PLACEHOLDER)>=0:
                buf = buf.replace(PLACEHOLDER+'/', url_prefix)

                with p.open('w', encoding='UTF-8') as f:
                    f.write(buf)

    folium_p = Path(args.folium)
    local_dir_p = Path(args.dir)
    url = args.url.rstrip('/') + '/'

    replace(folium_p, '**/*.py', url)
    # replace(local_dir_p, '**/*.css', url)

    # # In CSS files we can use relative URLs.
    # #
    # ix = url.index('//')
    # ix = url.index('/', ix+2)
    # url = url[ix:]
    # replace(local_dir_p, '**/*.css', url)

    # In CSS, files are relative to the CSS.
    # Therefore, since all of the downloaded files are in the same directory,
    # we only need the name.
    #
    ix = url.rindex('/')
    url = url[ix+1:]
    replace(local_dir_p, '**/*.css', '')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        prog='local_folium',
        description='Update folium .py files to use local versions of CDN files'
    )
    parser.add_argument('--folium', help='Root directory of folium files')
    parser.add_argument('--dir', help='The directory where the downloaded files will be put (optional)')
    subparsers = parser.add_subparsers(required=True)

    parser_process = subparsers.add_parser('process')
    # parser_process.add_argument('--folium', help='Root directory of folium files')
    # parser_process.add_argument('--dir', help='The directory where the downloaded files will be put (optional)')
    parser_process.set_defaults(func=_process)

    parser_replace = subparsers.add_parser('replace')
    # parser_process.add_argument('--folium', help='Root directory of folium files')
    # parser_replace.add_argument('--dir', help='The directory where the files were downloaded to')
    parser_replace.add_argument('--url', help='The base of the local URL')
    parser_replace.set_defaults(func=_replace)

    args = parser.parse_args()
    args.func(args)
