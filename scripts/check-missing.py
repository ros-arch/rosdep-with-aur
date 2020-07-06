#!/usr/bin/env python3
#
# Copyright (c) 2020 Hermann von Kleist
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.


import yaml
import tarfile
import urllib.request
import io
import gzip
import json


ROSDEP_YAML_FILE = "arch-with-aur.yaml"


def list_official_packages():
    pkgs = []
    for repo in ['core', 'extra', 'community']:
        with tarfile.open('/var/lib/pacman/sync/{}.db'.format(repo),
                          mode='r:gz') as db:
            for m in db.getmembers():
                if m.isfile():
                    next_line_has_name = False
                    for raw_line in db.extractfile(m).readlines():
                        line = raw_line.decode('utf-8').strip()
                        if next_line_has_name:
                            if len(line) > 0:
                                pkgs.append(line)
                            else:
                                next_line_has_name = False
                        elif '%NAME%' in line:
                            next_line_has_name = True
                        elif '%PROVIDES%' in line:
                            next_line_has_name = True
    return set(pkgs)


def list_aur_packages():
    with urllib.request.urlopen('https://aur.archlinux.org/packages.gz')\
            as res:
        stream = io.BytesIO(res.read())
        file = gzip.GzipFile(fileobj=stream)
        return set([line.decode('utf-8').strip() for line in file.readlines()])


def check_repology(key, rosdep_mappings):
    # Map rosdep os names to repology os identifiers
    os_lut = {
        'debian': {
            '*': 'debian_stable',
            'stretch': 'debian_oldstable',
            'buster': 'debian_stable',
        },
        'ubuntu': {
            '*': 'ubuntu_20_04',
            'bionic': 'ubuntu_18_04',
            'focal': 'ubuntu_20_04',
            'trusty': 'ubuntu_14_04',
            'xenial': 'ubuntu_16_04',
        }
    }

    def filter_hits(hits):
        hits = filter(lambda h: not h.endswith("-doc"), hits)
        hits = filter(lambda h: not h.endswith("-docs"), hits)
        hits = filter(lambda h: not h.endswith("-demos"), hits)

        hits = filter(lambda h: not h.endswith("-git"), hits)
        hits = filter(lambda h: not h.endswith("-svn"), hits)
        hits = filter(lambda h: not h.endswith("-hg"), hits)

        if key.startswith('python-'):
            # When our key starts with python-, it's a python 2 package.
            # So exclude arch linux python 3 packages, which also start
            # with python-. Yikes.
            hits = filter(lambda h: not h.startswith("python-"), hits)
        elif key.startswith('python3-'):
            hits = filter(lambda h: not h.startswith("python2-"), hits)
        return hits

    foreign_hits = {}
    for os in rosdep_mappings:
        if os in os_lut:
            if type(rosdep_mappings[os]) is dict:
                for osver in rosdep_mappings[os]:
                    if osver in os_lut[os]:
                        hits = rosdep_mappings[os][osver]
                        if type(hits) is list:
                            foreign_hits[os_lut[os][osver]] = hits
            elif rosdep_mappings[os] is not None:
                foreign_hits[os_lut[os]['*']] = rosdep_mappings[os]

    for os in foreign_hits:
        repo_hits = []
        aur_hits = []
        for pkgname in foreign_hits[os]:
            try:
                url = 'https://repology.org/tools/project-by?repo={}'\
                      '&name_type=binname&target_page=api_v1_project&name={}'\
                    .format(os, pkgname)
                with urllib.request.urlopen(url) as res:
                    data = json.loads(res.read())

                repo_hits.extend([d for d in data if d['repo'] == 'arch'])
                aur_hits.extend(
                    [d['binname'] for d in data if d['repo'] == 'aur'])
            except urllib.request.URLError:
                continue
            except yaml.YAMLError:
                continue

        core_hits = set(
            [h['binname'] for h in repo_hits if h['subrepo'] == 'core'])
        if len(core_hits) > 0:
            return filter_hits(core_hits)

        extra_hits = set(
            [h['binname'] for h in repo_hits if h['subrepo'] == 'extra'])
        if len(extra_hits) > 0:
            return filter_hits(extra_hits)

        community_hits = set(
            [h['binname'] for h in repo_hits if h['subrepo'] == 'community'])
        if len(community_hits) > 0:
            return filter_hits(community_hits)

        if len(aur_hits) > 0:
            return filter_hits(set(aur_hits))

    return []


def main():
    print("Loading pacman packages ...")
    official_packages = list_official_packages()
    print("{} pacman packages loaded.".format(len(official_packages)))

    print("Loading AUR packages ...")
    aur_packages = list_aur_packages()
    print("{} AUR packages loaded.".format(len(aur_packages)))

    try:
        print("Loading previous rosdep definitions ...")
        with open(ROSDEP_YAML_FILE) as prev_rosdep:
            previous_defs = yaml.safe_load(prev_rosdep.read())
        print("Previous definitions loaded.")
    except OSError:
        previous_defs = dict()
    except yaml.YAMLError:
        previous_defs = dict()

    def lookup_previous_defs(key):
        if key in previous_defs:
            if 'arch' in previous_defs[key]:
                if type(previous_defs[key]['arch']) is list:
                    return previous_defs[key]['arch']
        return []

    def do_all_pkgs_exist(pkgs):
        return all([p in official_packages | aur_packages for p in pkgs])

    stats = {
        "official": 0,
        "aur": 0,
        "repology": 0,
        "skipped": 0,
        "n/a": 0
    }
    new_keys = dict()
    for filename in ["base.yaml", "python.yaml"]:
        print("Loading {} ...".format(filename))
        url = 'https://raw.githubusercontent.com/ros/rosdistro/master/' \
              'rosdep/{}'.format(filename)
        with urllib.request.urlopen(url) as res:
            rd_map = yaml.safe_load(res.read())
            for key in rd_map:

                current_defs = lookup_previous_defs(key)
                # Keep current definitions if they are okay
                if len(current_defs) > 0 and do_all_pkgs_exist(current_defs):
                    new_keys[key] = {"arch": current_defs}
                    stats["skipped"] += 1
                    continue

                # Lookup official definitions
                if 'arch' in rd_map[key]:
                    if type(rd_map[key]['arch']) is list:
                        current_defs = rd_map[key]['arch']
                    # TODO: the type might be dict, in this case, there might
                    # be a different package manager available.
                else:
                    current_defs = []

                # Skipp current key if official definitions are okay
                if len(current_defs) > 0 and do_all_pkgs_exist(current_defs):
                    stats["skipped"] += 1
                    continue

                # To make a qualified guess, translate package prefixes for
                # python.
                if key.startswith('python-'):
                    guess = key.replace('python', 'python2', 1)
                elif key.startswith('python3-'):
                    guess = key.replace('python3', 'python', 1)
                else:
                    guess = key

                print("Looking for key {} ...".format(key))

                if guess in official_packages:
                    new_keys[key] = {"arch": [guess]}
                    stats["official"] += 1
                elif guess in aur_packages:
                    new_keys[key] = {"arch": [guess]}
                    stats["aur"] += 1
                else:
                    pkgs = list(check_repology(key, rd_map[key]))
                    if len(pkgs) > 0:
                        new_keys[key] = {"arch": pkgs}
                        stats["repology"] += 1
                    else:
                        new_keys[key] = {"arch": []}
                        stats["n/a"] += 1

    print("Stats: {} in official repositories, {} in AUR, {} found via "
          "repology, {} skipped, {} not found."
          .format(stats["official"], stats["aur"], stats["repology"],
                  stats["skipped"], stats["n/a"]))
    with open(ROSDEP_YAML_FILE, 'w') as out_file:
        yaml.safe_dump(new_keys, out_file)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
