"""This is a script for initializing the PUDL database locally."""

import os
import sys
import argparse

# This is a hack to make the pudl package importable from within this script,
# even though it isn't in one of the normal site-packages directories where
# Python typically searches.  When we have some real installation/packaging
# happening, this will no longer be necessary.
sys.path.append(os.path.abspath('..'))


def parse_command_line(argv):
    """
    Parse command line argument. See -h option.

    :param argv: arguments on the command line must include caller file name.
    """
    parser = argparse.ArgumentParser()

    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument(
        '-v', '--verbose', dest='verbose', action='store_true',
        help="Display messages indicating progress or errors.",
        default=True)
    verbosity.add_argument(
        '-q', '--quiet', dest='verbose', action='store_false',
        help="Suppress messages indicating progress or errors.")

    parser.add_argument('--keep_csv', dest='keep_csv', action='store_true',
                        help="Do not delete CSV files after loading them.",
                        default=False)
    parser.add_argument('--csvdir', dest='csvdir', type=str,
                        help="Path to directory for CSV file storage.",
                        default='')

    parser.add_argument('--ferc1_refyear', dest='ferc1_refyear', type=int,
                        default=2015,
                        help="Reference year for FERC Form 1 database.")
    parser.add_argument('--ferc1_start', dest='ferc1_start', type=int,
                        default=2007,
                        help="First year of FERC Form 1 data to load.")
    parser.add_argument('--ferc1_end', dest='ferc1_end', type=int,
                        default=2015,
                        help="Last year of FERC Form 1 data to load.")

    parser.add_argument('--eia923_start', dest='eia923_start', type=int,
                        default=2009,
                        help="First year of EIA Form 923 data to load.")
    parser.add_argument('--eia923_end', dest='eia923_end', type=int,
                        default=2015,
                        help="Last year of EIA Form 923 data to load.")

    arguments = parser.parse_args(argv[1:])

    return arguments


def main():
    """The main function."""
    from pudl import pudl, ferc1, eia923, settings, constants
    from pudl import models, models_ferc1, models_eia923

    args = parse_command_line(sys.argv)

    ferc1.init_db(ferc1_tables=constants.ferc1_default_tables,
                  refyear=args.ferc1_refyear,
                  years=range(args.ferc1_start, args.ferc1_end + 1),
                  def_db=True,
                  verbose=args.verbose,
                  testing=False)

    pudl.init_db(ferc1_tables=constants.ferc1_pudl_tables,
                 ferc1_years=range(args.ferc1_start, args.ferc1_end + 1),
                 eia923_tables=constants.eia923_pudl_tables,
                 eia923_years=range(args.eia923_start,
                                    args.eia923_end + 1),
                 verbose=args.verbose,
                 debug=False,
                 testing=False,
                 csvdir=args.csvdir,
                 keep_csv=args.keep_csv)


if __name__ == '__main__':
    sys.exit(main())