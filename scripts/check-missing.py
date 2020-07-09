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


import gzip
import io
import json
import os
import pickle
import tarfile
import urllib.error
import urllib.request

import yaml
from lxml import etree


ROSDEP_YAML_FILE = "arch-with-aur.yaml"


def get_cached(name):
    try:
        # TODO: Drop cache if it is too old (> 1 day)
        with open(os.path.join('cache', name + '.pickle'), mode='rb') as fstream:
            return pickle.load(fstream)
    except FileNotFoundError:
        return None
    except OSError:
        return None
    except pickle.PickleError:
        return None


def store_cache(name, obj):
    if not os.path.exists('cache'):
        os.makedirs('cache')
    with open(os.path.join('cache', name + '.pickle'), mode='w+b') as fstream:
        pickle.dump(obj, fstream)


def list_official_packages():
    cache = get_cached('arch_packages')
    if cache is not None:
        return cache

    pkgs = set()
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
                                pkgs.add(line)
                            else:
                                next_line_has_name = False
                        elif '%NAME%' in line:
                            next_line_has_name = True
                        elif '%PROVIDES%' in line:
                            next_line_has_name = True
    store_cache('arch_packages', pkgs)
    return pkgs


def list_aur_packages():
    cache = get_cached('aur_packages')
    if cache is not None:
        return cache

    with urllib.request.urlopen('https://aur.archlinux.org/packages.gz')\
            as res:
        stream = io.BytesIO(res.read())
        file = gzip.GzipFile(fileobj=stream)
        aur_pkgs = set([line.decode('utf-8').strip() for line in file.readlines()])

    store_cache('aur_packages', aur_pkgs)
    return aur_pkgs


def list_pip_packages():
    cache = get_cached('pip_packages')
    if cache is not None:
        return cache

    pkgs = set()
    with urllib.request.urlopen('https://pypi.org/simple/') as res:
        htmlroot = etree.fromstring(res.read())
        for child in htmlroot.findall('.//a'):
            pkgs.add(child.attrib['href'].split('/')[2])

    store_cache('pip_packages', pkgs)
    return pkgs


def load_rosdep_file(filename):
    print("Loading rosdep definitions from {} ...".format(filename))
    if '://' in filename:
        try:
            with urllib.request.urlopen(filename) as res:
                return yaml.safe_load(res.read())
        except urllib.error.URLError:
            return dict()
        except yaml.YAMLError:
            return dict()
    else:
        try:
            with open(filename) as fstream:
                return yaml.safe_load(fstream.read())
        except OSError:
            return dict()
        except yaml.YAMLError:
            return dict()


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
            except urllib.error.URLError:
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


def rosdep_lookup(yaml_data, key, os='arch', os_version=None,
                  pkg_manager=None):
    """
    How rosdep yaml files work (Yikes):

    1. Default pkg_manager, all os versions
    <rosdep_name>:
        <os_name>: [...]

    2. Default pkg_manager, specific os version
    <rosdep_name>:
        <os_name>:
            <os_version>: [...]

    3. Explicit pkg_manager, all os versions (REP 111)
    <rosdep_name>:
        <os_name>:
            <pkg_manager>:
                packages: [...]

    4. Explicit pkg_manager, specific os version (REP 111)
    <rosdep_name>:
        <os_name>:
            <os_version>:
                <pkg_manager>:
                    packages: [...]

    """
    if pkg_manager is None:
        try:
            pkgs = yaml_data[key][os]
            if type(pkgs) is list:
                return pkgs
        except KeyError:
            pass

        if os_version is not None:
            try:
                pkgs = yaml_data[key][os][os_version]
                if type(pkgs) is list:
                    return pkgs
            except KeyError:
                pass

        try:
            pkgs = yaml_data[key][os]['*']
            if type(pkgs) is list:
                return pkgs
        except KeyError:
            pass

    else:
        try:
            pkgs = yaml_data[key][os][pkg_manager]['packages']
            if type(pkgs) is list:
                return pkgs
        except KeyError:
            pass
        except TypeError:
            pass

        if os_version is not None:
            try:
                pkgs = yaml_data[key][os][os_version][pkg_manager]['packages']
                if type(pkgs) is list:
                    return pkgs
            except KeyError:
                pass
            except TypeError:
                pass

        try:
            pkgs = yaml_data[key][os]['*'][pkg_manager]['packages']
            if type(pkgs) is list:
                return pkgs
        except KeyError:
            pass
        except TypeError:
            pass

    return []


def main():
    print("Loading pacman packages ...")
    official_packages = list_official_packages()
    print("{} pacman packages loaded.".format(len(official_packages)))

    print("Loading AUR packages ...")
    aur_packages = list_aur_packages()
    print("{} AUR packages loaded.".format(len(aur_packages)))

    print("Loading PyPI packages ...")
    pip_packages = list_pip_packages()
    print("{} PyPI packages loaded".format(len(pip_packages)))

    previous_defs = load_rosdep_file(ROSDEP_YAML_FILE)

    def do_all_pkgs_exist(pkgs):
        return all([p in official_packages | aur_packages for p in pkgs])

    def do_all_pip_pkgs_exist(pkgs):
        return all([p in pip_packages for p in pkgs])

    stats = {
        "official": 0,
        "aur": 0,
        "pip": 0,
        "repology": 0,
        "skipped": 0,
        "n/a": 0
    }

    new_keys = dict()

    def add_definition(key, pkgs, pkg_manager=None):
        if pkg_manager is None:
            new_keys[key] = {
                'arch': sorted(pkgs)
            }
        else:
            new_keys[key] = {
                'arch': {
                    pkg_manager: {
                        'packages': sorted(pkgs)
                    }
                }
            }

    for filename in ["base.yaml", "python.yaml"]:
        url = 'https://raw.githubusercontent.com/ros/rosdistro/master/' \
              'rosdep/{}'.format(filename)
        official_rosdep_defs = load_rosdep_file(url)

        for key in official_rosdep_defs:
            # Keep current definitions if they are okay
            # Add current definitions to output file
            current_hits = rosdep_lookup(previous_defs, key)
            if len(current_hits) > 0 and do_all_pkgs_exist(current_hits):
                add_definition(key, current_hits)
                stats["skipped"] += 1
                continue

            current_pip_hits = rosdep_lookup(previous_defs, key,
                                             pkg_manager='pip')
            if len(current_pip_hits) > 0\
                    and do_all_pip_pkgs_exist(current_pip_hits):
                add_definition(key, current_pip_hits, pkg_manager='pip')
                stats["skipped"] += 1
                continue

            # Lookup official definitions
            # Do not add official definitions to output file
            official_hits = rosdep_lookup(official_rosdep_defs, key)
            if len(official_hits) > 0 and do_all_pkgs_exist(official_hits):
                stats["skipped"] += 1
                continue

            official_pip_hits = rosdep_lookup(official_rosdep_defs, key,
                                              pkg_manager='pip')
            if len(official_pip_hits) > 0\
                    and do_all_pip_pkgs_exist(official_pip_hits):
                stats["skipped"] += 1
                continue

            print("Looking for key {} ...".format(key))

            # To make a qualified guess, translate python package prefixes.
            if key.startswith('python-'):
                guess = key.replace('python', 'python2', 1)
            elif key.startswith('python3-'):
                guess = key.replace('python3', 'python', 1)
            else:
                guess = key

            if guess in official_packages:
                add_definition(key, [guess])
                stats["official"] += 1
                continue
            if guess in aur_packages:
                add_definition(key, [guess])
                stats["aur"] += 1
                continue
            if key.endswith("-pip"):
                # Guess pip packages after official/AUR packages to catch
                # python-pip and python2-pip.
                guess = key[:-4]
                if guess in pip_packages:
                    add_definition(key, [guess], pkg_manager='pip')
                    stats['pip'] += 1
                    continue
            pkgs = list(check_repology(key, official_rosdep_defs[key]))
            if len(pkgs) > 0:
                add_definition(key, pkgs)
                stats["repology"] += 1
                continue

            add_definition(key, [])
            stats["n/a"] += 1

    print("Stats: {} in official repositories, {} in AUR, {} on PyPI, "
          "{} found via repology, {} skipped, {} not found."
          .format(stats["official"], stats["aur"], stats["pip"],
                  stats["repology"], stats["skipped"], stats["n/a"]))
    with open(ROSDEP_YAML_FILE, 'w') as out_file:
        yaml.safe_dump(new_keys, out_file)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
