"""Finalize downloaded items without a local VFS mount (API / URL-only mode)."""

from loguru import logger

from program.core.runner import MediaItemGenerator, Runner, RunnerResult
from program.media.item import MediaItem
from program.media.state import States
from program.services.leaf_items import get_items_to_update


class FinalizeService(Runner[None]):
    """Marks leaf items with debrid media entries as completed (no Plex/library paths)."""

    def __init__(self):
        super().__init__()
        self.initialized = True

    @classmethod
    def get_key(cls) -> str:
        return "finalize"

    def validate(self) -> bool:
        return True

    def run(self, item: MediaItem) -> MediaItemGenerator:
        items_to_process = get_items_to_update(item)

        if not items_to_process:
            logger.debug(f"No downloaded leaf items to finalize for {item.log_string}")
            yield RunnerResult(media_items=[item])
            return

        for leaf in items_to_process:
            if not leaf.media_entry:
                logger.warning(
                    f"No media entry for {leaf.log_string}, resetting to Indexed"
                )

                MediaItem.store_state(leaf, States.Indexed)
                continue

            leaf.updated = True
            leaf.store_state()
            logger.debug(f"Finalized {leaf.log_string} (URL-only mode)")

        yield RunnerResult(media_items=[item])
