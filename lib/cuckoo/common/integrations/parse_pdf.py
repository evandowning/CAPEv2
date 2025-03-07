# Copyright (C) 2010-2015 Cuckoo Foundation, Optiv, Inc. (brad.spengler@optiv.com)
# This file is part of Cuckoo Sandbox - http://www.cuckoosandbox.org
# See the file 'docs/LICENSE' for copying permission.

import json
import logging
import os

from lib.cuckoo.common.integrations.peepdf import peepdf_parse
from lib.cuckoo.common.pdftools.pdfid import PDFiD, PDFiD2JSON

log = logging.getLogger(__name__)


class PDF(object):
    """PDF Analysis."""

    def __init__(self, file_path):
        self.file_path = file_path

    def _parse(self, filepath):
        """Parses the PDF for static information.
        @param filepath: Path to file to be analyzed.
        @return: results dict or None.
        """
        # Load the PDF with PDFiD and convert it to JSON for processing
        pdf_data = PDFiD(filepath, False, True)
        pdf_json = PDFiD2JSON(pdf_data, True)
        pdfid_data = json.loads(pdf_json)[0]

        info = {}
        info["PDF Header"] = pdfid_data["pdfid"]["header"]
        info["Total Entropy"] = pdfid_data["pdfid"]["totalEntropy"]
        info["Entropy In Streams"] = pdfid_data["pdfid"]["streamEntropy"]
        info["Entropy Out Streams"] = pdfid_data["pdfid"]["nonStreamEntropy"]
        info["Count %% EOF"] = pdfid_data["pdfid"]["countEof"]
        info["Data After EOF"] = pdfid_data["pdfid"]["countChatAfterLastEof"]
        # Note, PDFiD doesn't interpret some dates properly, specifically it doesn't
        # seem to be able to properly represent time zones that involve fractions of
        # an hour
        dates = pdfid_data["pdfid"]["dates"]["date"]

        # Get keywords, counts and format.
        keywords = {}
        for keyword in pdfid_data["pdfid"]["keywords"]["keyword"]:
            keywords[str(keyword["name"])] = keyword["count"]

        pdfresult = {}
        pdfresult["Info"] = info
        pdfresult["Dates"] = dates
        pdfresult["Keywords"] = keywords

        pdfresult = peepdf_parse(self.file_path, pdfresult)

        return pdfresult

    def run(self):
        """Run analysis.
        @return: analysis results dict or None.
        """
        if not os.path.exists(self.file_path):
            return None
        log.debug("Starting to load PDF")
        results = self._parse(self.file_path)
        return results
