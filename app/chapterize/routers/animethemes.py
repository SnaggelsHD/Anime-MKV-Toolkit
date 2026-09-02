import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.chapterize import animethemes

logger = logging.getLogger("chapterize.animethemes")

router = APIRouter()


@router.get("/search")
async def search(q: str = Query(..., min_length=1)):
    try:
        results = await animethemes.search_anime(q)
    except Exception as e:
        logger.exception("animethemes search failed")
        raise HTTPException(status_code=502, detail=f"animethemes.moe search failed: {e}")
    return [r.to_dict() for r in results]


@router.get("/{anime_slug}/themes")
async def themes(anime_slug: str):
    try:
        result = await animethemes.get_themes(anime_slug)
    except Exception as e:
        logger.exception("animethemes theme lookup failed")
        raise HTTPException(status_code=502, detail=f"animethemes.moe lookup failed: {e}")
    return [t.to_dict() for t in result]


class CacheRequest(BaseModel):
    anime_slug: str
    theme_slugs: list[str]


@router.post("/cache")
async def cache_themes(req: CacheRequest):
    try:
        all_themes = await animethemes.get_themes(req.anime_slug)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"animethemes.moe lookup failed: {e}")

    by_slug = {t.slug: t for t in all_themes}
    results = []
    for slug in req.theme_slugs:
        theme = by_slug.get(slug)
        if not theme:
            results.append({"theme_slug": slug, "ok": False, "error": "theme not found"})
            continue
        try:
            path = await animethemes.download_and_cache_theme(req.anime_slug, theme)
            results.append({"theme_slug": slug, "ok": True, "path": str(path)})
        except Exception as e:
            results.append({"theme_slug": slug, "ok": False, "error": str(e)})
    return results


@router.delete("/cache")
def clear_cache():
    removed = animethemes.clear_cache()
    return {"removed": removed}
