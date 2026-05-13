from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from kink import di
from loguru import logger

from pydantic import BaseModel
from sqlalchemy.orm import Session

from program.db.db import db_session
from program.media.media_entry import MediaEntry
from program.media.item import MediaItem
from program.services.streaming.exceptions import DebridServiceLinkUnavailable
from program.types import Event
from program.utils.debrid_cdn_url import DebridCDNUrl

if TYPE_CHECKING:
    from program.services.downloaders import Downloader


class GetEntryByOriginalFilenameResult(BaseModel):
    original_filename: str
    download_url: str | None
    unrestricted_url: str | None
    provider: str | None
    provider_download_id: str | None
    size: int | None
    created: str | None
    modified: str | None
    entry_type: Literal["media", "subtitle"]

    @property
    def url(self) -> str | None:
        """The URL to use for this request."""

        return self.unrestricted_url or self.download_url


class MediaEntryRegistry:
    """DB-backed media entry lookups and debrid URL refresh (no local mount)."""

    def __init__(self, downloader: Downloader | None = None) -> None:
        self.downloader = downloader

    def get_subtitle_content(
        self,
        parent_original_filename: str,
        language: str,
    ) -> bytes | None:
        with db_session() as session:
            from program.media.subtitle_entry import SubtitleEntry

            subtitle = (
                session.query(SubtitleEntry)
                .filter_by(
                    parent_original_filename=parent_original_filename, language=language
                )
                .first()
            )

            if subtitle and subtitle.content:
                return subtitle.content.encode("utf-8")

            return None

    def refresh_unrestricted_url(
        self,
        entry: MediaEntry,
        session: Session,
    ) -> str | None:
        if not self.downloader:
            logger.warning("No downloader available to refresh unrestricted URL")

            return None

        service = next(
            (
                svc
                for svc in self.downloader.services.values()
                if svc.key == entry.provider
            ),
            None,
        )

        if service and entry.download_url:
            try:
                new_unrestricted = service.unrestrict_link(entry.download_url)

                if new_unrestricted:
                    entry.unrestricted_url = new_unrestricted.download

                    cdn_url = DebridCDNUrl(entry)

                    if cdn_url.validate(attempt_refresh=False):
                        session.merge(entry)
                        session.commit()

                        logger.debug(
                            f"Refreshed unrestricted URL for {entry.original_filename}"
                        )

                        return entry.unrestricted_url
            except DebridServiceLinkUnavailable as e:
                logger.warning(
                    f"Failed to unrestrict URL for {entry.original_filename}: {e}"
                )

                if entry.media_item:
                    from program.program import Program as ProgramCls
                    from routers.secure.items import apply_item_mutation

                    def mutation(i: MediaItem, s: Session):
                        i.blacklist_active_stream()
                        i.reset()

                    apply_item_mutation(
                        program=di[ProgramCls],
                        item=entry.media_item,
                        mutation_fn=mutation,
                        session=session,
                    )

                    session.commit()

                    di[ProgramCls].em.add_event(
                        Event(
                            "StateTransition",
                            entry.media_item.id,
                        )
                    )

                    return None
                raise
            except Exception as e:
                logger.warning(
                    f"Unexpected error when unrestricting URL for {entry.original_filename}: {e}"
                )

        return None

    def get_entry_by_original_filename(
        self,
        original_filename: str,
        force_resolve: bool = False,
    ) -> GetEntryByOriginalFilenameResult | None:
        try:
            with db_session() as session:
                entry = (
                    session.query(MediaEntry)
                    .filter(MediaEntry.original_filename == original_filename)
                    .first()
                )

                if not entry:
                    return None

                download_url = entry.download_url
                unrestricted_url = entry.unrestricted_url

                if (force_resolve or not unrestricted_url) and (
                    self.downloader and entry.provider
                ):
                    unrestricted_url = self.refresh_unrestricted_url(
                        entry,
                        session=session,
                    )

                return GetEntryByOriginalFilenameResult(
                    original_filename=entry.original_filename,
                    download_url=download_url,
                    unrestricted_url=unrestricted_url,
                    provider=entry.provider,
                    provider_download_id=entry.provider_download_id,
                    size=entry.file_size,
                    created=(entry.created_at.isoformat()),
                    modified=(entry.updated_at.isoformat()),
                    entry_type="media",
                )
        except DebridServiceLinkUnavailable:
            raise
        except Exception as e:
            logger.error(
                f"Error getting entry by original_filename {original_filename}: {e}"
            )
            return None
