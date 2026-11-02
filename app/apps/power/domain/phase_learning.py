from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise
from statistics import median

from apps.power.domain.load_status import LoadReading
from apps.power.domain.loads import MEASURED_PHASES, LearnedPhase, LoadPhase
from apps.power.domain.phase_samples import LEVEL_SAMPLES, PhaseSample, mean_amps

PLUG_CURRENT_CHANGE = Decimal(2)
STEP_MARGIN = timedelta(seconds=3)
SAMPLE_GAP = timedelta(seconds=3)
VOTE_STEP = Decimal('1.5')
QUIET_STEP = Decimal('0.5')
VOTE_DELAY = timedelta(seconds=35)
VOTES_KEPT = 5
VOTES_NEEDED = 3
LEARNED_AMPS_STEP = Decimal('0.1')
LEARNED_AMPS_CHANGE = Decimal('0.5')


@dataclass(frozen=True, kw_only=True)
class LoadChange:
    load: str
    since: float
    until: float
    up: bool

    def overlaps(self, since: float, until: float) -> bool:
        return self.since < until and since < self.until


@dataclass(frozen=True, kw_only=True)
class PhaseStep:
    phase: LoadPhase
    at: float
    amps: Decimal
    others: Decimal


@dataclass(frozen=True, kw_only=True)
class PhaseVote:
    phase: LoadPhase
    amps: Decimal


def detect_change(load: str, previous: LoadReading, current: LoadReading) -> LoadChange | None:
    if current.read_at <= previous.read_at:
        return None

    if previous.closed is not None and current.closed is not None and previous.closed != current.closed:
        return LoadChange(load=load, since=previous.read_at, until=current.read_at, up=current.closed)

    if (
        previous.current is not None
        and current.current is not None
        and abs(current.current - previous.current) >= PLUG_CURRENT_CHANGE
    ):
        return LoadChange(
            load=load,
            since=previous.read_at,
            until=current.read_at,
            up=current.current > previous.current,
        )

    return None


def change_ripe(change: LoadChange, at: float) -> bool:
    return at - change.until >= VOTE_DELAY.total_seconds()


def find_step(samples: Sequence[PhaseSample], since: float, until: float) -> PhaseStep | None:
    last = until + STEP_MARGIN.total_seconds()
    steps = [
        step
        for index in range(LEVEL_SAMPLES, len(samples) - LEVEL_SAMPLES + 1)
        if since <= samples[index].at <= last
        for step in _steps_at(samples, index)
    ]
    return max(steps, key=lambda step: abs(step.amps), default=None)


def vote_for_change(
    change: LoadChange,
    samples: Sequence[PhaseSample],
    changes: Iterable[LoadChange],
) -> PhaseVote | None:
    if any(other.load != change.load and other.overlaps(change.since, change.until) for other in changes):
        return None

    margin = STEP_MARGIN.total_seconds()
    if not _covered([sample.at for sample in samples], change.since - margin, change.until + 2 * margin):
        return None

    step = find_step(samples, change.since, change.until)
    if step is None or abs(step.amps) < VOTE_STEP or (step.amps > 0) != change.up or step.others > QUIET_STEP:
        return None

    return PhaseVote(phase=step.phase, amps=abs(step.amps))


def remember_vote(votes: Sequence[PhaseVote], vote: PhaseVote) -> tuple[PhaseVote, ...]:
    return (*votes, vote)[-VOTES_KEPT:]


def tally_votes(votes: Sequence[PhaseVote]) -> LearnedPhase | None:
    recent = votes[-VOTES_KEPT:]
    if len(recent) < VOTES_NEEDED or len({vote.phase for vote in recent}) > 1:
        return None

    return LearnedPhase(
        phase=recent[0].phase,
        amps=median(vote.amps for vote in recent).quantize(LEARNED_AMPS_STEP, rounding=ROUND_HALF_UP),
        samples=len(recent),
    )


def learned_changed(stored: LearnedPhase | None, learned: LearnedPhase) -> bool:
    if stored is None or stored.phase is not learned.phase:
        return True

    return abs(stored.amps - learned.amps) >= LEARNED_AMPS_CHANGE


def _steps_at(samples: Sequence[PhaseSample], index: int) -> list[PhaseStep]:
    before = samples[index - LEVEL_SAMPLES : index]
    after = samples[index : index + LEVEL_SAMPLES]
    amps = {phase: mean_amps(after, phase) - mean_amps(before, phase) for phase in MEASURED_PHASES}
    return [
        PhaseStep(
            phase=phase,
            at=samples[index].at,
            amps=amps[phase],
            others=max(abs(amps[other]) for other in MEASURED_PHASES if other is not phase),
        )
        for phase in MEASURED_PHASES
    ]


def _covered(moments: Sequence[float], start: float, end: float) -> bool:
    if not moments or moments[0] > start or moments[-1] < end:
        return False

    gap = SAMPLE_GAP.total_seconds()
    return all(later - earlier <= gap for earlier, later in pairwise(moments) if later >= start and earlier <= end)
