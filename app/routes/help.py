from fastapi import APIRouter, Request

from ..web import render_page

router = APIRouter()


@router.get("")
async def help_page(request: Request):
    return render_page(request, "help.html", current_page="help")
