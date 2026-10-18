"""Which modules exist, and which a user has turned on (user_settings.enabled_modules)."""
from importlib import import_module

# name -> "package.module:Class". Imported only when a user has the module enabled.
AVAILABLE = {
    "training": "modules.training.module:TrainingModule",
    "nutrition": "modules.nutrition.module:NutritionModule",
}


def load(name: str, **kwargs):
    path, cls = AVAILABLE[name].split(":")
    return getattr(import_module(path), cls)(**kwargs)


def enabled_modules(user, **kwargs) -> list:
    """The user's enabled modules, in their configured order (unknown names are skipped)."""
    return [load(name, **kwargs) for name in user.enabled_modules if name in AVAILABLE]
