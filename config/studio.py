from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


CLIP_LENGTHS = {"short": (20, 60), "extended": (60, 180), "long": (180, 600)}
Platform = Literal["youtube", "instagram", "facebook"]
Text = Annotated[str, StringConstraints(strip_whitespace=True)]
Layout = Literal["smart_blur", "split_screen", "vertical_crop"]
MusicMode = Literal["original", "mute", "replace", "ai_replace"]


class Destination(BaseModel):
    account: Text = Field(default="", max_length=150)
    enabled: bool = False


class StudioSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    brand_name: Text = Field(default="", max_length=30)
    caption_color: str = Field(default="#DDF76D", pattern=r"^#[0-9a-fA-F]{6}$")
    caption_style: Literal["bold", "clean"] = "bold"
    captions: bool = True
    opening_hook: bool = True
    default_mode: Layout = "smart_blur"
    clip_length: Literal["short", "extended", "long"] = "short"
    max_clips_to_generate: int = Field(default=3, ge=1, le=6)
    llm_model: Text = Field(default="qwen2.5:7b", min_length=1, max_length=100)
    auto_delete_source: bool = False
    music_mode: MusicMode = "original"
    music_track: str = Field(default="", max_length=255)
    destinations: dict[Platform, Destination] = Field(default_factory=dict)


def studio_settings(settings: dict) -> StudioSettings:
    fields = StudioSettings.model_fields
    return StudioSettings.model_validate({key: value for key, value in settings.items() if key in fields})
