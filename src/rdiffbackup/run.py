# Copyright 2002, 2003, 2004, 2005 Ben Escoto
#
# This file is part of rdiff-backup.
#
# rdiff-backup is free software; you can redistribute it and/or modify
# under the terms of the GNU General Public License as published by the
# Free Software Foundation; either version 2 of the License, or (at your
# option) any later version.
#
# rdiff-backup is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with rdiff-backup; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301, USA
"""Start (and end) here - read arguments, set global settings, etc."""

import os
import sys
from rdiff_backup import Globals, log
from rdiffbackup import arguments, actions_mgr

if os.name == "nt":
    import msvcrt


def main():
    if os.name == "nt":
        # make sure line endings are kept under Windows like under Linux
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    sys.exit(main_run(sys.argv[1:]))


def main_run(arglist, security_override=False):
    """
    Main function to be called with arguments list without the
    name of the program, aka $0 resp. sys.argv[0].

    The security override is only meant for test purposes.

    Returns with an error code depending on the result.
    Check the man-page of the rdiff-backup binary for possible values
    and their meaning.
    """

    # get a dictionary of discovered action plugins
    discovered_actions = actions_mgr.get_actions_dict()

    # parse accordingly the arguments
    parsed_args = arguments.parse(
        arglist,
        "rdiff-backup {ver}".format(ver=Globals.version),
        actions_mgr.get_generic_parsers(),
        discovered_actions,
    )

    # we need verbosity set properly asap
    ret_val = log.Log.set_verbosity(
        parsed_args.get("verbosity"), parsed_args.get("terminal_verbosity")
    )
    if ret_val & Globals.RET_CODE_ERR:
        return ret_val

    # compatibility plug
    _parse_cmdlineoptions_compat201(parsed_args)

    # instantiate the action object from the dictionary, handing over the
    # parsed arguments
    action = discovered_actions[parsed_args["action"]](parsed_args)

    log.Log(
        "Runtime information =>{ri}<=".format(
            ri=action.get_runtime_info(parsed=parsed_args)
        ),
        log.DEBUG,
    )

    # validate that everything looks good before really starting
    ret_val |= action.pre_check()
    if ret_val & Globals.RET_CODE_ERR:
        log.Log(
            "Action {ac} failed on step {st}".format(
                ac=parsed_args["action"], st="pre_check"
            ),
            log.ERROR,
        )
        return ret_val

    # now start for real, conn_act and action are the same object
    with action.connect() as conn_act:
        if not conn_act.is_connection_ok():
            log.Log(
                "Action {ac} failed on step {st}".format(
                    ac=parsed_args["action"], st="connect"
                ),
                log.ERROR,
            )
            return conn_act.conn_status

        # For test purposes only, hence we allow ourselves to overwrite a
        # "private" variable
        if security_override:
            from rdiff_backup import Security

            Security._security_level = "override"

        ret_val |= conn_act.check()
        if ret_val & Globals.RET_CODE_ERR:
            log.Log(
                "Action {ac} failed on step {st}".format(
                    ac=parsed_args["action"], st="check"
                ),
                log.ERROR,
            )
            return ret_val

        ret_val |= conn_act.setup()
        if ret_val & Globals.RET_CODE_ERR:
            log.Log(
                "Action {ac} failed on step {st}".format(
                    ac=parsed_args["action"], st="setup"
                ),
                log.ERROR,
            )
            return ret_val

        ret_val |= conn_act.run()
        if ret_val & Globals.RET_CODE_ERR:
            log.Log(
                "Action {ac} failed on step {st}".format(
                    ac=parsed_args["action"], st="run"
                ),
                log.ERROR,
            )
            return ret_val

    # Give a final summary of what might have happened to the user
    if ret_val & Globals.RET_CODE_WARN:
        log.Log(
            "Action {ac} emitted warnings, "
            "see previous messages for details".format(ac=parsed_args["action"]),
            log.WARNING,
        )
    if ret_val & Globals.RET_CODE_FILE_ERR:
        log.Log(
            "Action {ac} failed on one or more files, "
            "see previous messages for details".format(ac=parsed_args["action"]),
            log.WARNING,
        )
    if ret_val & Globals.RET_CODE_FILE_WARN:
        log.Log(
            "Action {ac} emitted a warning on one or more files, "
            "see previous messages for details".format(ac=parsed_args["action"]),
            log.WARNING,
        )

    return ret_val


def _parse_cmdlineoptions_compat201(arglist):  # noqa: C901
    """
    Parse argument list and set global preferences, compatibility function
    between old and new way of parsing parameters.
    """

    # if action in ("backup", "restore"):
    Globals.set("acls_active", arglist.get("acls"))
    Globals.set("win_acls_active", arglist.get("acls"))
    Globals.set("carbonfile_active", arglist.get("carbonfile"))
    Globals.set("compare_inode", arglist.get("compare_inode"))
    Globals.set("eas_active", arglist.get("eas"))
    Globals.set("preserve_hardlinks", arglist.get("hard_links"))
    Globals.set("resource_forks_active", arglist.get("resource_forks"))
    Globals.set("never_drop_acls", arglist.get("never_drop_acls"))
    # if action in ("backup", "regress", "restore"):
    Globals.set("compression", arglist.get("compression"))
    # if action in ("backup"):
    Globals.set("file_statistics", arglist.get("file_statistics"))
    Globals.set("print_statistics", arglist.get("print_statistics"))
    # if action in ("regress"):
    Globals.set("allow_duplicate_timestamps", arglist.get("allow_duplicate_timestamps"))
    Globals.set("null_separator", arglist.get("null_separator"))
    Globals.set("use_compatible_timestamps", arglist.get("use_compatible_timestamps"))
    Globals.set("do_fsync", arglist.get("fsync"))
    if arglist["action"] in ("server"):
        Globals.server = True
    if arglist.get("current_time") is not None:
        Globals.set_integer("current_time", arglist.get("current_time"))
    if arglist.get("chars_to_quote") is not None:
        Globals.set("chars_to_quote", os.fsencode(arglist.get("chars_to_quote")))
    if arglist.get("api_version") is not None:  # FIXME catch also env variable?
        Globals.set_api_version(arglist.get("api_version"))


if __name__ == "__main__":
    main()
