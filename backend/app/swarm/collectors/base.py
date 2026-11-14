"""Shared types for swarm collectors. A collector turns a public post link into the same Post list an
upload produces; detection never knows where the posts came from."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

from app.swarm.ingest import MAX_POSTS, MAX_TEXT_CHARS, Post, parse_time


@dataclass
class Collected:
    platform: str
    url: str
    posts: list[Post] = field(default_factory=list)
    root: dict | None = None            # the linked post: {author, text, url, created}
    notes: list[str] = field(default_factory=list)
    requests: int = 0
    complete: bool = True               # False when a limit (posts, requests, time, platform cap) cut it short
    failed: list[dict] = field(default_factory=list)   # multi-link: [{url, reason}]
    no_text: int = 0                    # image/video-only posts: nothing to compare, skipped
    _ids: set = field(default_factory=set, repr=False)

    def add(self, post: Post) -> bool:
        """Append unless the post cap is reached; returns False when full."""
        if not post.text.strip():
            if post.id not in self._ids:
                self._ids.add(post.id)
                self.no_text += 1
            return True
        if len(self.posts) >= MAX_POSTS:
            self.complete = False
            return False
        if post.id not in self._ids:
            self._ids.add(post.id)
            self.posts.append(post)
        return True

    def summary(self) -> dict:
        return {"platform": self.platform, "url": self.url, "root": self.root, "posts": len(self.posts),
                "accounts": len({p.author.lower() for p in self.posts}), "requests": self.requests,
                "complete": self.complete, "failed": self.failed, "without_text": self.no_text}


def make_post(pid: str, author: str, created: str | None, text: str, *, url: str | None = None,
              author_created: str | None = None, platform: str) -> Post | None:
    ts = parse_time(created)
    if ts is None or not author:
        return None
    return Post(id=pid, author=author[:100], ts=ts, text=(text or "")[:MAX_TEXT_CHARS], url=url,
                author_created_at=parse_time(author_created) if author_created else None, platform=platform)


_TAG = re.compile(r"<[^>]+>")
_BREAK = re.compile(r"<\s*(br|/p|/li)\s*/?>", re.I)


def html_to_text(content: str) -> str:
    """Mastodon/YouTube HTML -> plain text ("@<span>user</span>" -> "@user", links keep their text)."""
    t = _BREAK.sub("\n", content or "")
    t = _TAG.sub("", t)
    return re.sub(r"[ \t]+", " ", html.unescape(t)).strip()
