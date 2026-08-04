import importlib
import pkgutil
import os
from typing import List
from backend.modules.base_module import BaseModule
from backend.ops.logger import logger
def get_all_modules() -> List[BaseModule]:
    """Dynamically loads and returns all workflow modules in this directory."""
    modules = []
    package_dir = os.path.dirname(__file__)
    for _, module_name, _ in pkgutil.iter_modules([package_dir]):
        if module_name == "base_module":
            continue
        try:
            mod = importlib.import_module(f"backend.modules.{module_name}")
            # Look for an instantiated BaseModule object in the module
            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                # Ensure it is an instance of BaseModule but NOT the class itself
                if isinstance(attr, BaseModule) and type(attr) is not BaseModule:
                    modules.append(attr)
                    break # Only load one instance per file
        except Exception as e:
            logger.warning(f"Failed to load module backend.modules.{module_name}: {e}")
    return modules