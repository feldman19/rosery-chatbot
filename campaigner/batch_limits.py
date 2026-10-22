"""One bounded owner-reviewed launch, shared by intake and execution."""
MAX_ADSETS = 3
MAX_ADS = 15
MAX_CREATIVE_IMAGES = MAX_ADS * 2
MAX_DRIVE_SOURCES = MAX_CREATIVE_IMAGES
# Local Meta upload transport bound; analysis-only source support is separate.
MAX_UPLOAD_IMAGE_BYTES = 20 * 1024 * 1024
