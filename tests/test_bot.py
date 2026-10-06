from dataclasses import dataclass

import discord

from .fakes import FakeUser


@dataclass
class SyncedCommand:
    name: str
    id: int


async def test_commands_are_synced_only_when_they_change(bot, tmp_path):
    bot.settings.database_path = tmp_path / "bot.db"
    calls = []

    async def sync(*, guild=None):
        calls.append("sync")
        return [SyncedCommand("help", 1)]

    async def fetch_commands(*, guild=None):
        calls.append("fetch")
        return [SyncedCommand("help", 1)]

    bot.tree.sync, bot.tree.fetch_commands = sync, fetch_commands
    await bot.sync_commands()
    await bot.sync_commands()
    assert calls == ["sync", "fetch"] and bot.command_ids == {"help": 1}

    bot.tree.remove_command("help")
    await bot.sync_commands()
    assert calls[-1] == "sync"


class Notice:
    def __init__(self, type, author_id) -> None:
        self.type = type
        self.author = FakeUser(id=author_id)
        self.channel = FakeUser(id=1)
        self.deleted = False

    async def delete(self) -> None:
        self.deleted = True


async def test_the_bots_own_pin_notices_are_deleted(bot, monkeypatch):
    monkeypatch.setattr(type(bot), "user", property(lambda _self: FakeUser(id=10)))
    processed = []

    async def process_commands(message):
        processed.append(message)

    bot.process_commands = process_commands
    ours, theirs = Notice(discord.MessageType.pins_add, 10), Notice(discord.MessageType.pins_add, 11)
    await bot.on_message(ours)
    await bot.on_message(theirs)
    assert ours.deleted and not theirs.deleted
    assert processed == [theirs]
