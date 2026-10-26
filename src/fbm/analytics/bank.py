"""Common-random-number bank of simulated single-game stat lines.

Each (player, key) gets its own deterministic RNG stream, so two candidate rosters that share players reuse the exact
same simulated games. Comparisons between candidates (streaming moves, trades) then reflect the roster change, not
Monte Carlo noise.
"""

from __future__ import annotations

import hashlib
from collections.abc import Hashable, Iterable, Mapping

import numpy as np

from fbm.analytics.simulation import COL, STATS, PlayerModel, form_multiplier


class DrawBank:
    def __init__(self, models: Mapping[str, PlayerModel], n_sims: int = 2000, seed: int = 17):
        self.models = models
        self.n = n_sims
        self.seed = seed
        self._games: dict[tuple[str, Hashable], np.ndarray] = {}
        self._weeks: dict[tuple[str, int], np.ndarray] = {}
        self._forms: dict[str, np.ndarray] = {}

    def _rng(self, pid: str, key: Hashable) -> np.random.Generator:
        digest = hashlib.blake2b(f"{pid}|{key}".encode(), digest_size=8).hexdigest()
        return np.random.default_rng([self.seed, int(digest, 16)])

    def _form(self, pid: str, model: PlayerModel) -> np.ndarray | None:
        """One weekly role/form multiplier per simulation, shared by all of the player's games (same stream for
        every candidate roster)."""
        if model.form_sd <= 0:
            return None
        arr = self._forms.get(pid)
        if arr is None:
            arr = form_multiplier(self._rng(pid, "__form__"), self.n, model.form_sd).astype(np.float32)
            self._forms[pid] = arr
        return arr

    def game(self, pid: str, key: Hashable) -> np.ndarray:
        """Simulated stat lines for one game of `pid` (shape n x len(STATS)); zeros if the player sits.

        `key` is normally the game's ET date (enables back-to-back rest risk)."""
        cache_key = (pid, key)
        arr = self._games.get(cache_key)
        if arr is None:
            model = self.models.get(pid)
            p = 0.0 if model is None else model.game_play_prob(key)
            if model is None or p <= 0:
                arr = np.zeros((self.n, len(STATS)), np.float32)
            else:
                rng = self._rng(pid, key)
                arr = model.samples[rng.integers(0, len(model.samples), self.n)].astype(np.float32)
                if p < 1:
                    arr *= (rng.random(self.n) < p)[:, None]
                form = self._form(pid, model)
                if form is not None:
                    arr *= form[:, None]
            self._games[cache_key] = arr
        return arr

    def total(
        self, player_games: Iterable[tuple[str, Hashable]], banked: Mapping[str, float] | None = None
    ) -> np.ndarray:
        out = np.zeros((self.n, len(STATS)))
        for pid, key in player_games:
            out += self.game(pid, key)
        for stat, value in (banked or {}).items():
            if stat in COL:
                out[:, COL[stat]] += value
        return out

    def generic_week(self, pid: str, games: int = 4) -> np.ndarray:
        """Sum of `games` independent games — a schedule-neutral week for season-level comparisons."""
        key = (pid, games)
        arr = self._weeks.get(key)
        if arr is None:
            arr = np.zeros((self.n, len(STATS)), np.float32)
            for i in range(games):
                arr += self.game(pid, ("generic", i))
            self._weeks[key] = arr
        return arr
