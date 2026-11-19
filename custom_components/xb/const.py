"""Constants for XBloom integration."""

DOMAIN = "xb"

# Config entry keys
CONF_MAC_ADDRESS = "mac_address"
CONF_TELEMETRY_INTERVAL = "telemetry_interval"
CONF_SESSION_TIMEOUT = "session_timeout"
CONF_RECIPES = "recipes"
CONF_WATER_SOURCE = "water_source"   # persisted in entry.options
CONF_MODE = "mode"                   # persisted in entry.options
CONF_WEIGHT_UNIT = "weight_unit"     # persisted in entry.options
CONF_TEMP_UNIT = "temp_unit"         # persisted in entry.options

# One-time recipe seed flags (entry.options). The local recipe store is the
# source of truth; the cloud is only consulted once per install (and once
# more when an account is added later) — see coordinator.async_seed_recipes.
CONF_RECIPES_SEEDED = "recipes_seeded"
CONF_ACCOUNT_RECIPES_SEEDED = "account_recipes_seeded"

# entry.options: what HA last wrote to each Easy Mode slot —
# {"A": {"uid": ..., "name": ...}, ...}. The machine never reports slot
# contents, so this record is the only source for the slot sensor entities.
CONF_EASY_SLOTS = "easy_slots"

# entry.options: which XBloom web store the panel's shop links open —
# "global" / "kr" / "cn", absent = auto (from Home Assistant's country).
# Mirrors the official app's region setting (RegionLanguageActivity).
CONF_STORE_REGION = "store_region"

# XBloom cloud account — both optional. Absent entirely (not just empty
# strings) when the user skips the account step; cloud-backed services/LLM
# tools must check for their absence and fail gracefully, never assume they
# exist. Stored in entry.data (identity/credentials), not entry.options.
CONF_EMAIL = "email"
CONF_PASSWORD = "password"

# Defaults
DEFAULT_TELEMETRY_INTERVAL = 5  # seconds
DEFAULT_SESSION_TIMEOUT = 0     # seconds; 0 = stay connected
DEFAULT_WATER_SOURCE = 0        # 0 = tank
DEFAULT_MODE = "easy"
DEFAULT_WEIGHT_UNIT = "g"
DEFAULT_TEMP_UNIT = "c"

# Data keys
DATA_COORDINATOR = "coordinator"

# Services
SERVICE_EXECUTE_RECIPE = "execute_recipe"
# Tea recipes take a completely different BLE sequence (brewing.py's
# _async_brew_tea) and none of execute_recipe's coffee-only fields
# (dose/ratio/grind/bypass) apply — a separate, leaner service avoids
# exposing them for a tea brew. Shares ATTR_RECIPE with execute_recipe /
# create_recipe / edit_recipe so recipe targeting stays consistent.
SERVICE_EXECUTE_TEA_RECIPE = "execute_tea_recipe"
ATTR_GRIND_SIZE = "grind_size"
ATTR_RPM = "rpm"
ATTR_DOSE_G = "dose_g"
ATTR_RATIO = "ratio"
ATTR_BYPASS_VOLUME = "bypass_volume"
ATTR_BYPASS_TEMPERATURE = "bypass_temperature"

# Cross-identifier field shared by every recipe-addressing service: accepts
# a local uid, a cloud table id, a share URL/id, or the exact recipe name
# (resolution order in schema.find_recipe).
ATTR_RECIPE = "recipe"

# Local recipe store services — the local store (entry.options[CONF_RECIPES])
# is the source of truth; these never touch the cloud.
SERVICE_LIST_RECIPES = "list_recipes"
ATTR_QUERY = "query"

SERVICE_CREATE_RECIPE = "create_recipe"
ATTR_RECIPE_YAML = "recipe_yaml"

SERVICE_EDIT_RECIPE = "edit_recipe"
ATTR_CHANGES = "changes"

SERVICE_DELETE_RECIPE = "delete_recipe"

SERVICE_WRITE_RECIPE_TO_EASY_SLOT = "write_recipe_to_easy_slot"
SERVICE_WRITE_RECIPE_TO_AUTO_MODE = "write_recipe_to_auto_mode"
SERVICE_RESET_AUTO_MODE = "reset_auto_mode"
ATTR_SLOT = "slot"

SERVICE_ADVANCED_SETTINGS = "advanced_settings"
ATTR_POUR_RADIUS_LEVEL = "pour_radius_level"
ATTR_VIBRATION_AMPLITUDE_LEVEL = "vibration_amplitude_level"
ATTR_DISPLAY_BRIGHTNESS_LEVEL = "display_brightness_level"

# Cloud boundary services (cloud_ prefix = the network is involved).
SERVICE_CLOUD_IMPORT_RECIPE = "cloud_import_recipe"
ATTR_SHARE_URL = "share_url"
ATTR_RECIPE_ID = "recipe_id"

SERVICE_CLOUD_EXPORT_RECIPE = "cloud_export_recipe"

# Public collective.xbloom.com community recipe hub search — a separate,
# unauthenticated API from the rest of the cloud_* services above (which all
# act on the user's own private cloud account). See _cloud_client.py's
# COLLECTIVE_API_BASE module comment.
SERVICE_CLOUD_SEARCH_COLLECTIVE_RECIPES = "cloud_search_collective_recipes"
ATTR_KEYWORD = "keyword"
ATTR_CATEGORY = "category"
ATTR_SRC = "src"
ATTR_MACHINE = "machine"
ATTR_CUP_TYPE = "cup_type"
ATTR_ORIGIN = "origin"
ATTR_VARIETAL = "varietal"
ATTR_PROCESS = "process"
ATTR_ROAST = "roast"
ATTR_FLAVOR = "flavor"
ATTR_SORT = "sort"
ATTR_SORT_DIRECTION = "sort_direction"

# LLM API identifiers
XBLOOM_LLM_API_ID = "xbloom_coffee"
XBLOOM_LLM_API_NAME = "xBloom Coffee Machine"

# ── Device type (config entry) ────────────────────────────────────────────
# Studio (J15) is the original BLE path; Original (J20) is Wi-Fi + cloud
# only. Entries created before this key existed are Studio entries.
CONF_DEVICE_TYPE = "device_type"
DEVICE_TYPE_STUDIO = "studio"
DEVICE_TYPE_ORIGINAL = "original"

# Original (J20) config-entry data. The account credentials are the same
# xBloom app login; the password is only used to log in again when the
# app's refresh-token exchange fails (refresh tokens expire too).
CONF_J20_DEVICE_ID = "j20_device_id"
CONF_J20_PRODUCT_ID = "j20_product_id"
CONF_J20_SERIAL_NUMBER = "j20_serial_number"
CONF_J20_NAME = "j20_name"
CONF_J20_MAC = "j20_mac"  # 12 lower-case hex digits, when known

# Original Wi-Fi onboarding form (never stored in the entry).
CONF_WIFI_SSID = "wifi_ssid"
CONF_WIFI_PASSWORD = "wifi_password"

DEFAULT_J20_SCAN_INTERVAL = 15  # seconds between thing-shadow polls

# Studio: the last six characters of the serial number, as the machine
# advertises them in its Bluetooth name ("XBLOOM 4CV030"). Read once at setup
# so the device name is stable; absent when the name couldn't be read.
CONF_SERIAL_SUFFIX = "serial_suffix"
