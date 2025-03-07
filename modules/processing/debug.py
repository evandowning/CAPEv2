# Copyright (C) 2010-2015 Cuckoo Foundation.
# This file is part of Cuckoo Sandbox - http://www.cuckoosandbox.org
# See the file 'docs/LICENSE' for copying permission.

from __future__ import absolute_import
import codecs
import os

from lib.cuckoo.common.abstracts import Processing
from lib.cuckoo.common.exceptions import CuckooProcessingError
from lib.cuckoo.core.database import Database


class Debug(Processing):
    """Analysis debug information."""

    def run(self):
        """Run debug analysis.
        @return: debug information dict.
        """
        self.key = "debug"
        debug = {"log": "", "errors": []}

        if os.path.exists(self.log_path):
            try:
                debug["log"] = codecs.open(self.log_path, "rb", "utf-8").read()
            except ValueError as e:
                raise CuckooProcessingError(f"Error decoding {self.log_path}: {e}") from e
            except (IOError, OSError) as e:
                raise CuckooProcessingError(f"Error opening {self.log_path}: {e}") from e

        for error in Database().view_errors(int(self.task["id"])):
            debug["errors"].append(error.message)

        return debug
