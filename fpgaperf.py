#!/usr/bin/env python3

import os
import subprocess
import time
import collections
import json
import re
import shutil
import sys
import glob
import datetime
import edalize
from terminaltables import AsciiTable

from toolchain import Toolchain
from utils import Timed

from icestorm import Nextpnr
from icestorm import Arachne
from vivado import Vivado
from vivado import VivadoYosys
from symbiflow import VPR

# to find data files
root_dir = os.path.dirname(os.path.abspath(__file__))
project_dir = root_dir + '/project'
src_dir = root_dir + '/src'


class NotAvailable:
    pass


# https://stackoverflow.com/questions/377017/test-if-executable-exists-in-python
def which(program):
    def is_exe(fpath):
        return os.path.isfile(fpath) and os.access(fpath, os.X_OK)

    fpath, fname = os.path.split(program)
    if fpath:
        if is_exe(program):
            return program
    else:
        for path in os.environ["PATH"].split(os.pathsep):
            exe_file = os.path.join(path, program)
            if is_exe(exe_file):
                return exe_file

    return None


def have_exec(mybin):
    return which(mybin) != None


# no seed support?
class Icecube2(Toolchain):
    '''Lattice Icecube2 based toolchains'''
    carries = None

    ICECUBEDIR_DEFAULT = os.getenv("ICECUBEDIR", "/opt/lscc/iCEcube2.2017.08")

    def __init__(self):
        Toolchain.__init__(self)
        self.icecubedir = self.ICECUBEDIR_DEFAULT

    def run(self):
        with Timed(self, 'bit-all'):
            print('top: %s' % self.top)
            env = os.environ.copy()
            env["SRCS"] = ' '.join(self.srcs)
            env["TOP"] = self.top
            env["ICECUBEDIR"] = self.icecubedir
            #env["ICEDEV"] = 'hx8k-ct256'
            env["ICEDEV"] = self.device + '-' + self.package
            args = "--syn %s" % (self.syn(), )
            if self.strategy:
                args += " --strategy %s" % (self.strategy, )
            self.cmd(root_dir + "/icecubed.sh", args, env=env)

            self.cmd("iceunpack", "my.bin my.asc")

        self.cmd("icetime", "-tmd %s my.asc" % (self.device, ))

    def max_freq(self):
        with open(self.out_dir + '/icetime.txt') as f:
            return icetime_parse(f)['max_freq']

    def resources(self):
        return icebox_stat("my.asc", self.out_dir)

    @staticmethod
    def asc_ver(f):
        '''
        .comment
        Lattice
        iCEcube2 2017.08.27940
        Part: iCE40HX1K-TQ144
        Date: Jun 27 2018 13:22:06
        '''
        for l in f:
            if l.find('iCEcube2') == 0:
                return l.split()[1].strip()
        assert 0

    def versions(self):
        # FIXME: see if can get from tool
        asc_ver = None
        try:
            with open(self.out_dir + '/my.asc') as ascf:
                asc_ver = Icecube2.asc_ver(ascf)
        except FileNotFoundError:
            pass

        return {
            'yosys': yosys_ver(),
            'icecube2': asc_ver,
        }


class Icecube2Synpro(Icecube2):
    '''Lattice Icecube2 using Synplify for synthesis'''
    carries = (True, )

    def __init__(self):
        Icecube2.__init__(self)
        self.toolchain = 'icecube2-synpro'

    def syn(self):
        return "synpro"

    @staticmethod
    def check_env():
        return {
            'ICECUBEDIR': os.path.exists(Icecube2.ICECUBEDIR_DEFAULT),
            'icetime': have_exec('icetime'),
        }


class Icecube2LSE(Icecube2):
    '''Lattice Icecube2 using LSE for synthesis'''
    carries = (True, )

    def __init__(self):
        Icecube2.__init__(self)
        self.toolchain = 'icecube2-lse'

    def syn(self):
        return "lse"

    @staticmethod
    def check_env():
        return {
            'ICECUBEDIR': os.path.exists(Icecube2.ICECUBEDIR_DEFAULT),
            'icetime': have_exec('icetime'),
        }


class Icecube2Yosys(Icecube2):
    '''Lattice Icecube2 using Yosys for synthesis'''
    carries = (True, False)

    def __init__(self):
        Icecube2.__init__(self)
        self.toolchain = 'icecube2-yosys'

    def syn(self):
        return "yosys-synpro"

    @staticmethod
    def check_env():
        return {
            'yosys': have_exec('yosys'),
            'ICECUBEDIR': os.path.exists(Icecube2.ICECUBEDIR_DEFAULT),
            'icetime': have_exec('icetime'),
        }


# .asc version field just says "DiamondNG"
# guess that was the code name...
# no seed support? -n just does more passes
class Radiant(Toolchain):
    '''Lattice Radiant based toolchains'''
    carries = None
    # FIXME: strategy isn't being set correctly
    # https://github.com/SymbiFlow/fpga-tool-perf/issues/14
    strategies = ('Timing', 'Quick', 'Area')

    RADIANTDIR_DEFAULT = os.getenv("RADIANTDIR", "/opt/lscc/radiant/1.0")

    def __init__(self):
        Toolchain.__init__(self)
        self.radiantdir = Radiant.RADIANTDIR_DEFAULT

    def run(self):
        # acceptable for either device
        assert (self.device, self.package) in [
            ('up3k', 'uwg30'), ('up5k', 'uwg30'), ('up5k', 'sg48')
        ]

        with Timed(self, 'bit-all'):
            env = os.environ.copy()
            env["SRCS"] = ' '.join(self.srcs)
            env["TOP"] = self.top
            env["RADIANTDIR"] = self.radiantdir
            env["RADDEV"] = self.device + '-' + self.package
            syn = self.syn()
            args = "--syn %s" % (syn, )
            if self.strategy:
                args += " --strategy %s" % self.strategy
            self.cmd(root_dir + "/radiant.sh", args, env=env)

            self.cmd("iceunpack", "my.bin my.asc")

        self.cmd("icetime", "-tmd up5k my.asc")

    def max_freq(self):
        with open(self.out_dir + '/icetime.txt') as f:
            return icetime_parse(f)['max_freq']

    def resources(self):
        return icebox_stat("my.asc", self.out_dir)

    def radiant_ver(self):
        # a lot of places where this is, but not sure whats authoritative
        for l in open(self.radiantdir + '/data/ispsys.ini'):
            # ./data/ispsys.ini:19:ProductType=1.0.0.350.6
            if l.find('ProductType') == 0:
                return l.split('=')[1].strip()
        assert 0

    def versions(self):
        return {
            'yosys': yosys_ver(),
            'radiant': self.radiant_ver(),
        }

    @staticmethod
    def check_env():
        return {
            'RADIANTDIR': os.path.exists(Radiant.RADIANTDIR_DEFAULT),
            'iceunpack': have_exec('iceunpack'),
            'icetime': have_exec('icetime'),
        }


class RadiantLSE(Radiant):
    '''Lattice Radiant using LSE for synthesis'''
    carries = (True, )

    def __init__(self):
        Radiant.__init__(self)
        self.toolchain = 'radiant-lse'

    def syn(self):
        return "lse"


class RadiantSynpro(Radiant):
    '''Lattice Radiant using Synplify for synthesis'''
    carries = (True, )

    def __init__(self):
        Radiant.__init__(self)
        self.toolchain = 'radiant-synpro'

    def syn(self):
        return "synplify"


# @E: CG389 :"/home/mcmaster/.../impl/impl.v":18:4:18:7|Reference to undefined module SB_LUT4
# didn't look into importing edif
class RadiantYosys(Radiant):
    carries = (True, False)

    def __init__(self):
        Radiant.__init__(self)
        self.toolchain = 'radiant-yosys'

    def syn(self):
        return "yosys-synpro"


def print_stats(t):
    def print_section_header(title):
        print('')
        print('===============================')
        print(title)
        print('===============================')
        print('')

    print_section_header('Setting')

    table_data = [
        ['Settings', 'Value'],
        ['Design', t.design()],
        ['Family', t.family],
        ['Device', t.device],
        ['Package', t.package],
        ['Project', t.project_name],
        ['Toolchain', t.toolchain],
        ['Strategy', t.strategy],
        ['Carry', t.carry],
    ]

    if t.seed:
        table_data.append(['Seed', ('0x%08X (%u)' % (t.seed, t.seed))])
    else:
        table_data.append(['Seed', 'default'])

    table = AsciiTable(table_data)
    print(table.table)

    print_section_header('Clocks')
    max_freq = t.max_freq()
    table_data = [
        [
            'Clock domain', 'Actual freq', 'Requested freq', 'Met?',
            'Setup violation', 'Hold violation'
        ]
    ]
    if type(max_freq) is float:
        table_data.append(['Design', ("%0.3f MHz" % (max_freq / 1e6))])
    elif type(max_freq) is dict:
        for cd in max_freq:
            actual = "%0.3f MHz" % (max_freq[cd]['actual'] / 1e6)
            requested = "%0.3f MHz" % (max_freq[cd]['requested'] / 1e6)
            met = max_freq[cd]['met']
            s_violation = ("%0.3f ns" % max_freq[cd]['setup_violation'])
            h_violation = ("%0.3f ns" % max_freq[cd]['hold_violation'])
            table_data.append(
                [cd, actual, requested, met, s_violation, h_violation]
            )

    table = AsciiTable(table_data)
    print(table.table)

    print_section_header('Toolchain Resource Usage')
    table_data = [['Stage', 'Run Time (seconds)']]
    for k, v in t.runtimes.items():
        if type(v) is collections.OrderedDict:
            table_data.append([k, ""])
            for k1, v1 in v.items():
                table_data.append(["    " + k1, ("%0.3f" % v1)])
        else:
            table_data.append([k, ("%0.3f" % v)])

    table = AsciiTable(table_data)
    print(table.table)

    print_section_header('FPGA resource utilization')
    table_data = [['Resource', 'Used']]

    for k, v in sorted(t.resources().items()):
        table_data.append([k, v])

    table = AsciiTable(table_data)
    print(table.table)


toolchains = {
    'vivado': Vivado,
    'vivado-yosys': VivadoYosys,
    'arachne': Arachne,
    'vpr': VPR,
    'nextpnr': Nextpnr,
    'icecube2-synpro': Icecube2Synpro,
    'icecube2-lse': Icecube2LSE,
    'icecube2-yosys': Icecube2Yosys,
    'radiant-synpro': RadiantSynpro,
    'radiant-lse': RadiantLSE,
    #'radiant-yosys':    RadiantYosys,
    #'radiant': VPR,
}


def run(
    family,
    device,
    package,
    toolchain,
    project,
    out_dir=None,
    out_prefix=None,
    verbose=False,
    strategy=None,
    seed=None,
    carry=None,
    build=None
):
    assert family == 'ice40' or family == 'xc7'
    assert device is not None
    assert package is not None
    assert toolchain is not None
    assert project is not None
    # some toolchains use signed 32 bit
    assert seed is None or 1 <= seed <= 0x7FFFFFFF

    t = toolchains[toolchain](root_dir)
    t.verbose = verbose
    t.strategy = strategy
    t.seed = seed
    t.carry = carry

    #print("TJUR out_dir: " + out_dir)
    # Contraint files shall be in their directories
    pcf = get_pcf(project, family, device,
                  package, toolchain)
    sdc = get_sdc(project, family, device,
                  package, toolchain)
    xdc = get_xdc(project, family, device,
                  package, toolchain)


    # XXX: sloppy path handling here...
    t.pcf = os.path.realpath(pcf) if pcf else None
    t.sdc = os.path.realpath(sdc) if sdc else None
    t.xdc = os.path.realpath(xdc) if sdc else None
    t.build = build

    t.project(
        get_project(project),
        family,
        device,
        package,
        out_dir=out_dir,
        out_prefix=out_prefix,
    )

    t.run()
    print_stats(t)
    t.write_metadata()


def get_toolchains():
    '''Query all supported toolchains'''
    return sorted(toolchains.keys())


def list_toolchains():
    '''Print all supported toolchains'''
    for t in get_toolchains():
        print(t)


def get_projects():
    '''Query all supported projects'''
    return sorted(
        [
            re.match('/.*/(.*)[.]json', fn).group(1)
            for fn in glob.glob(project_dir + '/*.json')
        ]
    )


def list_projects():
    '''Print all supported projects'''
    for project in get_projects():
        print(project)


def get_seedable():
    '''Query toolchains that support --seed'''
    ret = []
    for t, tc in sorted(toolchains.items()):
        if tc.seedable():
            ret.append(t)
    return ret


def list_seedable():
    '''Print toolchains that support --seed'''
    for t in get_seedable():
        print(t)


def check_env(to_check=None):
    '''For each tool, print dependencies and if they are met'''
    tools = toolchains.items()
    if to_check:
        tools = list(filter(lambda t: t[0] == to_check, tools))
        if not tools:
            raise TypeError("Unknown toolchain %s" % to_check)
    for t, tc in sorted(tools):
        print(t)
        for k, v in tc.check_env().items():
            print('  %s: %s' % (k, v))


def env_ready():
    '''Return true if every tool can be ran'''
    for tc in toolchains.values():
        for v in tc.check_env().values():
            if not v:
                return False
    return True

def get_constraint(project, family, device, package, toolchain, extension):
    path = src_dir + '/'   \
           + project + '/' \
           + family + '/'  \
           + device + package + '/' \
           + toolchain + '.' + extension
    if(os.path.exists(path)):
        return path
    else:
        return None


def get_pcf(project, family, device, package, toolchain):
    return get_constraint(project, family, device, package, toolchain, 'pcf')


def get_sdc(project, family, device, package, toolchain):
    return get_constraint(project, family, device, package, toolchain, 'sdc')


def get_xdc(project, family, device, package, toolchain):
    return get_constraint(project, family, device, package, toolchain, 'xdc')


def get_project(name):
    project_fn = project_dir + '/' + name + '.json'
    with open(project_fn, 'r') as f:
        return json.load(f)


def add_bool_arg(parser, yes_arg, default=False, **kwargs):
    dashed = yes_arg.replace('--', '')
    dest = dashed.replace('-', '_')
    parser.add_argument(
        yes_arg, dest=dest, action='store_true', default=default, **kwargs
    )
    parser.add_argument(
        '--no-' + dashed, dest=dest, action='store_false', **kwargs
    )


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description=
        'Analyze FPGA tool performance (MHz, resources, runtime, etc)'
    )

    parser.add_argument('--verbose', action='store_true', help='')
    parser.add_argument('--overwrite', action='store_true', help='')
    parser.add_argument('--family', default='ice40', help='Device family')
    parser.add_argument(
        '--device', default='hx8k', help='Device within family'
    )
    parser.add_argument('--package', default=None, help='Device package')
    parser.add_argument(
        '--strategy', default=None, help='Optimization strategy'
    )
    add_bool_arg(
        parser,
        '--carry',
        default=None,
        help='Force carry / no carry (default: use tool default)'
    )
    parser.add_argument(
        '--toolchain', help='Tools to use', choices=get_toolchains()
    )
    parser.add_argument('--list-toolchains', action='store_true', help='')
    parser.add_argument(
        '--project', help='Source code to run on', choices=get_projects()
    )
    parser.add_argument('--list-projects', action='store_true', help='')
    parser.add_argument(
        '--seed',
        default=None,
        help='31 bit seed number to use, possibly directly mapped to PnR tool'
    )
    parser.add_argument('--list-seedable', action='store_true', help='')
    parser.add_argument(
        '--check-env',
        action='store_true',
        help='Check if environment is present'
    )
    parser.add_argument('--out-dir', default=None, help='Output directory')
    parser.add_argument(
        '--out-prefix',
        default=None,
        help='Auto named directory prefix (default: build)'
    )
    parser.add_argument('--build', default=None, help='Build number')
    args = parser.parse_args()

    if args.list_toolchains:
        list_toolchains()
    elif args.list_projects:
        list_projects()
    elif args.list_seedable:
        list_seedable()
    elif args.check_env:
        check_env(args.toolchain)
    else:
        argument_errors = []
        if args.family is None:
            argument_errors.append('--family argument required')
        if args.device is None:
            argument_errors.append('--device argument required')
        if args.package is None:
            argument_errors.append('--package argument required')
        if args.toolchain is None:
            argument_errors.append('--toolchain argument required')
        if args.project is None:
            argument_errors.append('--project argument required')

        if argument_errors:
            parser.print_usage()
            for e in argument_errors:
                print('{}: error: {}'.format(sys.argv[0], e))
            sys.exit(1)

        seed = int(args.seed, 0) if args.seed else None
        run(
            args.family,
            args.device,
            args.package,
            args.toolchain,
            args.project,
            out_dir=args.out_dir,
            out_prefix=args.out_prefix,
            strategy=args.strategy,
            carry=args.carry,
            seed=seed,
            build=args.build,
            verbose=args.verbose
        )


if __name__ == '__main__':
    main()
