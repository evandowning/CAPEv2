import hashlib
import json
import logging
import os
import shutil
import subprocess
import tempfile

from lib.cuckoo.common.config import Config
from lib.cuckoo.common.constants import CUCKOO_ROOT
from lib.cuckoo.common.integrations.parse_dotnet import DotNETExecutable
from lib.cuckoo.common.integrations.parse_java import Java
from lib.cuckoo.common.integrations.parse_lnk import LnkShortcut
from lib.cuckoo.common.integrations.parse_office import HAVE_OLETOOLS, Office

# ToDo duplicates logging here
from lib.cuckoo.common.integrations.parse_pdf import PDF
from lib.cuckoo.common.integrations.parse_pe import HAVE_PEFILE, PortableExecutable
from lib.cuckoo.common.integrations.parse_wsf import WindowsScriptFile  # EncodedScriptFile
from lib.cuckoo.common.objects import File

# from lib.cuckoo.common.integrations.parse_elf import ELF
from lib.cuckoo.common.utils import get_options, is_text_file

log = logging.getLogger(__name__)

logging.getLogger("Kixtart-Detokenizer").setLevel(logging.CRITICAL)

try:
    from lib.cuckoo.common.integrations.Kixtart.detokenize import Kixtart

    HAVE_KIXTART = True
except ImportError:
    HAVE_KIXTART = False

try:
    from lib.cuckoo.common.integrations.vbe_decoder import decode_file as vbe_decode_file

    HAVE_VBE_DECODER = True
except ImportError:
    HAVE_VBE_DECODER = False

try:
    from batch_deobfuscator.batch_interpreter import BatchDeobfuscator, handle_bat_file

    batch_deobfuscator = BatchDeobfuscator()
    HAVE_BAT_DECODER = True
except ImportError:
    HAVE_BAT_DECODER = False
    print("Missed dependency: pip3 install -U git+https://github.com/DissectMalware/batch_deobfuscator")

processing_conf = Config("processing")
selfextract_conf = Config("selfextract")

# Replace with DIE
if processing_conf.trid.enabled:
    trid_binary = os.path.join(CUCKOO_ROOT, processing_conf.trid.identifier)
    definitions = os.path.join(CUCKOO_ROOT, processing_conf.trid.definitions)


def static_file_info(data_dictionary: dict, file_path: str, task_id: str, package: str, options: str, destination_folder: str):

    if (
        not HAVE_OLETOOLS
        and "Zip archive data, at least v2.0" in data_dictionary["type"]
        and package in ("doc", "ppt", "xls", "pub")
    ):
        log.info("Missed dependencies: pip3 install oletools")

    if HAVE_PEFILE and ("PE32" in data_dictionary["type"] or "MS-DOS executable" in data_dictionary["type"]):
        data_dictionary["pe"] = PortableExecutable(file_path).run(task_id)
        if "Mono" in data_dictionary["type"]:
            data_dictionary["dotnet"] = DotNETExecutable(file_path).run()
    elif HAVE_OLETOOLS and package in ("doc", "ppt", "xls", "pub"):
        # options is dict where we need to get pass get_options
        data_dictionary["office"] = Office(file_path, task_id, data_dictionary["sha256"], get_options(options)).run()
    elif "PDF" in data_dictionary["type"] or file_path.endswith(".pdf"):
        data_dictionary["pdf"] = PDF(file_path).run()
    elif package == "wsf" or data_dictionary["type"] == "XML document text" or file_path.endswith(".wsf") or package == "hta":
        data_dictionary["wsf"] = WindowsScriptFile(file_path).run()
    # elif package == "js" or package == "vbs":
    #    static = EncodedScriptFile(file_path).run()
    elif package == "lnk":
        data_dictionary["lnk"] = LnkShortcut(file_path).run()
    elif "Java Jar" in data_dictionary["type"] or file_path.endswith(".jar"):
        if selfextract_conf.procyon.binary and not os.path.exists(selfextract_conf.procyon.binary):
            log.error("procyon_path specified in processing.conf but the file does not exist")
        data_dictionary["java"] = Java(file_path, selfextract_conf.procyon.binary).run()

    # It's possible to fool libmagic into thinking our 2007+ file is a zip.
    # So until we have static analysis for zip files, we can use oleid to fail us out silently,
    # yeilding no static analysis results for actual zip files.
    # elif file_path.endswith(".elf") or "ELF" in thetype:
    #    data_dictionary["elf"] = ELF(file_path).run()
    #    data_dictionary["keys"] = f.get_keys()
    # elif HAVE_OLETOOLS and package in ("hwp", "hwp"):
    #    data_dictionary["hwp"] = HwpDocument(file_path).run()

    with open(file_path, "rb") as f:
        is_text_file(data_dictionary, file_path, 8192, f.read())

    generic_file_extractors(file_path, destination_folder, data_dictionary["type"], data_dictionary)

    if processing_conf.trid.enabled:
        trid_info(file_path, data_dictionary)

    if processing_conf.die.enabled:
        detect_it_easy_info(file_path, data_dictionary)


def detect_it_easy_info(file_path, data_dictionary):
    if not os.path.exists(processing_conf.die.binary):
        return

    try:
        output = subprocess.check_output(
            [processing_conf.die.binary, "-j", file_path], stderr=subprocess.STDOUT, universal_newlines=True
        )
        if "detects" not in output:
            return

        strings = []
        for block in json.loads(output).get("detects", []) or []:
            strings += [sub["string"] for sub in block.get("values", [])]

        if strings:
            data_dictionary["die"] = strings
    except subprocess.CalledProcessError:
        log.warning("You need to configure your server to make TrID work properly")
        log.warning("sudo rm -f /usr/lib/locale/locale-archive && sudo locale-gen --no-archive")


def trid_info(file_path, data_dictionary):

    try:
        output = subprocess.check_output(
            [trid_binary, f"-d:{definitions}", file_path], stderr=subprocess.STDOUT, universal_newlines=True
        )
        data_dictionary["trid"] = output.split("\n")[6:-1]
    except subprocess.CalledProcessError:
        log.warning("You need to configure your server to make TrID work properly")
        log.warning("sudo rm -f /usr/lib/locale/locale-archive && sudo locale-gen --no-archive")


def _extracted_files_metadata(folder, destination_folder, data_dictionary, content=False, files=False):
    """
    args:
        folder - where files extracted
        destination_folder - where to move extracted files
        files - file names
    """
    metadata = []
    if not files:
        files = os.listdir(folder)
    for file in files:
        full_path = os.path.join(folder, file)
        file_details = File(full_path).get_all()
        if file_details:
            file_details = file_details[0]

        if processing_conf.trid.enabled:
            trid_info(full_path, file_details)

        if processing_conf.die.enabled:
            detect_it_easy_info(full_path, file_details)

        metadata.append(file_details)
        dest_path = os.path.join(destination_folder, file_details["sha256"])
        if not os.path.exists(dest_path):
            shutil.move(full_path, dest_path)

    return metadata


def generic_file_extractors(file, destination_folder, filetype, data_dictionary):
    """
    file - path to binary
    destination_folder - where to move extracted files
    filetype - magic string
    data_dictionary - where to add data

    Run all extra extractors/unpackers/extra scripts here, each extractor should check file header/type/identification:
    """

    for funcname in (
        msi_extract,
        kixtart_extract,
        vbe_extract,
        batch_extract,
        UnAutoIt_extract,
        RarSFX_extract,
        UPX_unpack,
        NSIS_unpack,
        Inno_extract,
    ):

        if not getattr(selfextract_conf, funcname.__name__).get("enabled", False):
            continue

        try:
            funcname(file, destination_folder, filetype, data_dictionary)
        except Exception as e:
            log.error(e, exc_info=True)


def _generic_post_extraction_process(file, decoded, destination_folder, data_dictionary, tool_name):
    with tempfile.TemporaryDirectory(prefix=tool_name) as tempdir:
        decoded_file_path = os.path.join(tempdir, f"{os.path.basename(file)}_decoded")
        with open(decoded_file_path, "wb") as f:
            f.write(decoded)

    metadata = []
    metadata += _extracted_files_metadata(tempdir, destination_folder, data_dictionary, files=[decoded_file_path])
    if metadata:
        for meta in metadata:
            is_text_file(meta, destination_folder, 8192)

        data_dictionary.setdefault("decoded_files", metadata)
        data_dictionary.setdefault("decoded_files_tool", tool_name)


def batch_extract(file, destination_folder, filetype, data_dictionary):
    # https://github.com/DissectMalware/batch_deobfuscator
    # https://www.fireeye.com/content/dam/fireeye-www/blog/pdfs/dosfuscation-report.pdf

    if not HAVE_BAT_DECODER or not file.endswith(".bat"):
        return

    decoded = handle_bat_file(batch_deobfuscator, file)
    if not decoded:
        return

    # compare hashes to ensure that they are not the same
    with open(file, "rb") as f:
        data = f.read()

    original_sha256 = hashlib.sha256(data).hexdigest()
    decoded_sha256 = hashlib.sha256(decoded).hexdigest()

    if original_sha256 == decoded_sha256:
        return

    _generic_post_extraction_process(file, decoded, destination_folder, data_dictionary, "Batch")


def vbe_extract(file, destination_folder, filetype, data_dictionary):

    if not HAVE_VBE_DECODER:
        log.debug("Missed VBE decoder")
        return

    decoded = False

    with open(file, "rb") as f:
        data = f.read()

    if b"#@~^" not in data[:100]:
        return

    try:
        decoded = vbe_decode_file(file, data)
    except Exception as e:
        log.error(e, exc_info=True)

    if not decoded:
        log.debug("VBE content wasn't decoded")
        return

    _generic_post_extraction_process(file, decoded, destination_folder, data_dictionary, "Vbe")


def msi_extract(file, destination_folder, filetype, data_dictionary, msiextract="/usr/bin/msiextract"):  # dropped_path
    """Work on MSI Installers"""

    if "MSI Installer" not in filetype:
        return

    if not os.path.exists(msiextract):
        logging.error("Missed dependency: sudo apt install msitools")
        return

    metadata = []

    with tempfile.TemporaryDirectory(prefix="msidump_") as tempdir:
        try:
            files = subprocess.check_output([msiextract, file, "--directory", tempdir], universal_newlines=True)
            if files:
                files = [
                    extracted_file
                    for extracted_file in list(filter(None, files.split("\n")))
                    if os.path.isfile(os.path.join(tempdir, extracted_file))
                ]
                metadata += _extracted_files_metadata(tempdir, destination_folder, data_dictionary, files=files)

        except Exception as e:
            logging.error(e, exc_info=True)

    if metadata:
        for meta in metadata:
            is_text_file(meta, destination_folder, 8192)

        data_dictionary.setdefault("extracted_files", metadata)
        data_dictionary.setdefault("extracted_files_tool", "MsiExtract")


def Inno_extract(file, destination_folder, filetype, data_dictionary):
    """Work on Inno Installers"""

    if "die" not in data_dictionary or not any(["Inno Setup" in string for string in data_dictionary["die"]]):
        return

    if not os.path.exists(selfextract_conf.Inno_extract.binary):
        logging.error("Missed dependency: sudo apt install innoextract")
        return

    metadata = []

    with tempfile.TemporaryDirectory(prefix="innoextract_") as tempdir:
        try:
            _ = subprocess.check_output(
                [selfextract_conf.Inno_extract.binary, file, "--output-dir", tempdir], universal_newlines=True
            )

            files = []
            for root, _, filenames in os.walk(tempdir):
                for file in filenames:
                    file = os.path.join(root, file)
                    if not os.path.isfile(file):
                        continue
                    files.append(file)

            metadata += _extracted_files_metadata(tempdir, destination_folder, data_dictionary, files=files)
        except subprocess.CalledProcessError:
            logging.error("Can't unpack InnoSetup for %s", file)
        except Exception as e:
            logging.error(e, exc_info=True)

    if metadata:
        for meta in metadata:
            is_text_file(meta, destination_folder, 8192)

        data_dictionary.setdefault("extracted_files", metadata)
        data_dictionary.setdefault("extracted_files_tool", "InnoExtract")


def kixtart_extract(file, destination_folder, filetype, data_dictionary):
    """
    https://github.com/jhumble/Kixtart-Detokenizer/blob/main/detokenize.py
    """

    if not HAVE_KIXTART:
        return

    with open(file, "rb") as f:
        content = f.read()

    metadata = []

    if content.startswith(b"\x1a\xaf\x06\x00\x00\x10"):
        with tempfile.TemporaryDirectory(prefix="kixtart_") as tempdir:
            kix = Kixtart(file, dump_dir=tempdir)
            kix.decrypt()
            kix.dump()

            metadata += _extracted_files_metadata(tempdir, destination_folder, data_dictionary, content=content)

    if metadata:
        for meta in metadata:
            is_text_file(meta, destination_folder, 8192)

        data_dictionary.setdefault("extracted_files", metadata)
        data_dictionary.setdefault("extracted_files_tool", "Kixtart")


def UnAutoIt_extract(file, destination_folder, filetype, data_dictionary):

    if not any([block.get("name") == "AutoIT_Compiled" for block in data_dictionary.get("yara")]):
        return

    if not os.path.exists(selfextract_conf.UnAutoIt_extract.binary):
        log.warning(
            f"Missed UnAutoIt binary: {selfextract_conf.UnAutoIt_extract.binary}. You can download a copy from - https://github.com/x0r19x91/UnAutoIt"
        )
        return

    metadata = list()

    with tempfile.TemporaryDirectory(prefix="unautoit_") as tempdir:
        try:
            output = subprocess.check_output(
                [selfextract_conf.UnAutoIt_extract.binary, "extract-all", "--output-dir", tempdir, file], universal_newlines=True
            )
            if output:
                files = [
                    os.path.join(tempdir, extracted_file)
                    for extracted_file in tempdir
                    if os.path.isfile(os.path.join(tempdir, extracted_file))
                ]
                metadata += _extracted_files_metadata(tempdir, destination_folder, data_dictionary, files=files)
        except subprocess.CalledProcessError:
            logging.error("Can't unpack AutoIT for %s", file)
        except Exception as e:
            logging.error(e, exc_info=True)

    if metadata:
        for meta in metadata:
            is_text_file(meta, destination_folder, 8192)

        data_dictionary.setdefault("extracted_files", metadata)
        data_dictionary.setdefault("extracted_files_tool", "UnAutoIt")


def RarSFX_extract(file, destination_folder, filetype, data_dictionary):

    if "RAR self-extracting archive" not in data_dictionary.get("type", ""):
        return

    if not os.path.exists(selfextract_conf.RarSFX_extract.binary):
        log.warning(f"Missed UnRar binary: {selfextract_conf.RarSFX_extract.binary}. sudo apt install unrar")
        return

    metadata = list()

    with tempfile.TemporaryDirectory(prefix="unrar_") as tempdir:
        try:
            output = subprocess.check_output([selfextract_conf.RarSFX_extract.binary, "e", file, tempdir], universal_newlines=True)
            if output:
                files = [
                    os.path.join(tempdir, extracted_file)
                    for extracted_file in tempdir
                    if os.path.isfile(os.path.join(tempdir, extracted_file))
                ]
                metadata += _extracted_files_metadata(tempdir, destination_folder, data_dictionary, files=files)

        except subprocess.CalledProcessError:
            logging.error("Can't unpack SFX for %s", file)
        except Exception as e:
            logging.error(e, exc_info=True)

    if metadata:
        for meta in metadata:
            is_text_file(meta, destination_folder, 8192)

        data_dictionary.setdefault("extracted_files", metadata)
        data_dictionary.setdefault("extracted_files_tool", "UnRarSFX")


def UPX_unpack(file, destination_folder, filetype, data_dictionary):

    # ToDo maybe check yara for UPX?
    # hit["name"] == "UPX":
    if "UPX compressed" not in filetype:
        return

    metadata = list()

    with tempfile.TemporaryDirectory(prefix="unupx_") as tempdir:
        try:
            dest_path = f"{os.path.join(tempdir, os.path.basename(file))}_unpacked"
            output = subprocess.check_output(
                [
                    "upx",
                    "-d",
                    file,
                    f"-o{dest_path}",
                ],
                universal_newlines=True,
            )
            if output and "Unpacked 1 file." in output:
                metadata += _extracted_files_metadata(tempdir, destination_folder, data_dictionary, files=[dest_path])
        except subprocess.CalledProcessError:
            logging.error("Can't unpack UPX for %s", file)

        except Exception as e:
            logging.error(e, exc_info=True)

    if metadata:
        for meta in metadata:
            is_text_file(meta, destination_folder, 8192)

        data_dictionary.setdefault("extracted_files", metadata)
        data_dictionary.setdefault("extracted_files_tool", "UnUPX")


def NSIS_unpack(file, destination_folder, filetype, data_dictionary):

    if "Nullsoft Installer self-extracting archive" not in filetype:
        return

    metadata = list()

    with tempfile.TemporaryDirectory(prefix="unnsis_") as tempdir:
        try:
            output = subprocess.check_output(
                [
                    "7z",
                    "e",
                    file,
                    f"-o{tempdir}",
                ],
                universal_newlines=True,
            )
            if output:
                files = [
                    os.path.join(tempdir, extracted_file)
                    for extracted_file in tempdir
                    if os.path.isfile(os.path.join(tempdir, extracted_file))
                ]
                metadata += _extracted_files_metadata(tempdir, destination_folder, data_dictionary, files=files)

        except subprocess.CalledProcessError:
            logging.error("Can't unpack NSIS for %s", file)

        except Exception as e:
            logging.error(e, exc_info=True)

    if metadata:
        for meta in metadata:
            is_text_file(meta, destination_folder, 8192)

        data_dictionary.setdefault("extracted_files", metadata)
        data_dictionary.setdefault("extracted_files_tool", "UnNSIS")
