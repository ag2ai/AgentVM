from typing import Dict, Type

from .base import AppEnv


class VSCodeEnv(AppEnv):
    pass


class GoogleChromeEnv(AppEnv):
    pass


class LibreOfficeWriterEnv(AppEnv):
    pass


class LibreOfficeCalcEnv(AppEnv):
    pass


class LibreOfficeImpressEnv(AppEnv):
    pass


class VLCEnv(AppEnv):
    pass


APP_ENV_REGISTRY: Dict[str, Type[AppEnv]] = {
    "vscode": VSCodeEnv,
    "google_chrome": GoogleChromeEnv,
    "libreoffice_writer": LibreOfficeWriterEnv,
    "libreoffice_calc": LibreOfficeCalcEnv,
    "libreoffice_impress": LibreOfficeImpressEnv,
    "vlc": VLCEnv,
}


