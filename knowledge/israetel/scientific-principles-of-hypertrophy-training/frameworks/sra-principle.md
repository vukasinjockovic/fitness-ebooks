# SRA Principle (Stimulus Recovery Adaptation)

**Type:** framework
**Status:** authoritative
**Last Updated:** 2026-02-03
**Aliases:** Stimulus Recovery Adaptation, SRA Curve, Recovery-Adaptation Cycle

## Summary

The SRA principle describes the fundamental cycle by which training produces muscle growth: a stimulus is applied, recovery occurs, adaptation follows, and then the system is ready for the next stimulus. This cycle dictates optimal training frequency by predicting when a muscle is ready to be trained again for best results.

## The SRA Cycle

```
Performance
    ^
    |     Adaptation
    |         /\
    |        /  \        Ready for
    |       /    \       next stimulus
Baseline -----    --------->
    |     \    /
    |      \  / Recovery
    |       \/
    |    Fatigue
    |
    +----------------------------> Time
         Stimulus
```

### Phase 1: Stimulus
- Training creates disruption
- Performance temporarily drops below baseline
- Muscle growth signaling activated

### Phase 2: Recovery
- Resources used to repair damage
- Performance returns toward baseline
- Takes longer than adaptation in most cases

### Phase 3: Adaptation
- Muscle growth occurs (supercompensation)
- Performance rises slightly above baseline
- Window for optimal re-stimulation

### Phase 4: Detraining (if no stimulus)
- Adaptations begin to reverse
- Performance returns toward baseline
- Missed training opportunity

## Key Insight: Recovery vs Adaptation Timing

For most intermediate and advanced lifters:
- **Adaptation (growth)**: Completes in 1-3 days post-training
- **Recovery (fatigue clearance)**: Takes 2-4 days or longer

**Implication:** Recovery is typically the limiting factor for training frequency, not adaptation.

## Factors Affecting SRA Duration

### Muscle-Specific Factors

| Factor | Faster Recovery | Slower Recovery |
|--------|-----------------|-----------------|
| Muscle size | Smaller muscles | Larger muscles |
| Fiber type | More slow-twitch | More fast-twitch |
| Architecture | Multi-pennate, multi-directional | Uni-directional |
| Stretch position | Limited stretch under load | Large stretch under load |

**Fast-Recovering Muscles:** Side delts, rear delts, biceps, forearms, calves
**Slow-Recovering Muscles:** Quads, hamstrings, pecs, lats, glutes

### Training Factors

| Factor | Faster Recovery | Slower Recovery |
|--------|-----------------|-----------------|
| Volume | Lower sets | Higher sets |
| Load | Lighter (20-30 reps) | Heavier (5-10 reps) |
| RIR | Higher RIR (4-5) | Lower RIR (0-1) |
| Exercise type | Isolation, machines | Compound, free weight |

### Individual Factors

| Factor | Faster Recovery | Slower Recovery |
|--------|-----------------|-----------------|
| Training age | Beginner | Advanced |
| Sex | Female | Male |
| Sleep/nutrition | Optimal | Suboptimal |
| Life stress | Low | High |

## Optimal Training Frequency

Based on SRA principles, most lifters benefit from:

### General Guidelines
- **2-4 sessions per muscle per week** for most muscles
- **3-8 sets per muscle per session** on average
- Lower end for bigger/slower-recovering muscles
- Higher end for smaller/faster-recovering muscles

### Per-Session Volume Caps
- **Average MAV:** 5-10 sets per muscle per session
- **Upper limit:** ~15 sets per muscle per session
- **Total session:** ~25-30 sets maximum

### Research Summary
1. 1x/week: Suboptimal (even volume-equated)
2. 2x/week: Good baseline
3. 3x/week: Better than 2x by notable margin
4. 4x/week: Better than 3x by smaller margin
5. 5x/week: Better than 4x by minimal margin

## Frequency-Deriving Algorithm (Full)

1. Train muscle at session-MEV
2. Wait until no longer sore/tired, train again
3. Check if performance is at/above baseline
   - Yes: Note time interval (this is your frequency)
   - No: Add one day next time
4. Adjust volumes via Set Progression Algorithm
5. After 2-5 sessions, you have your frequency

## Simpler Frequency-Deriving Algorithm

1. Start at 2x/week per muscle at MEV
2. Progress to ~10 sets per session
3. Note soreness/healing timeline
4. Note performance throughout
5. If healed with time to spare: increase frequency
6. If overlapping soreness/performance loss: decrease frequency

## Common Mistakes

### Under-Application
- Training only 1x/week for all muscles
- Missing growth opportunities during adaptation window
- Excessive per-session volume causing junk volume

### Over-Application
- Training same muscle daily without recovery
- Supporting muscles never getting rest
- Overlapping soreness and declining performance
- Connective tissue damage accumulation

## Key Quotes

> "In most cases, 2-4 overloading sessions per week per muscle group are possible to execute sustainably."

> "For most intermediate and advanced lifters, fatigue takes longer to reduce than adaptation (growth) takes to occur."

> "Around 3-8 sets per muscle group per session and 2-4 sessions per muscle group per week is likely a good average starting point."

## Sources in Collection

| Book | Author | How It's Used | Citation |
|------|--------|---------------|----------|
| Scientific Principles of Hypertrophy Training | Israetel et al. | Central framework | Ch.4 (SRA) |
| The Muscle Ladder | Nippard | Simplified as training frequency | Ch.5 |

## Related Entities

- [Volume Landmarks](../concepts/volume-landmarks.md) - Per-session MAV limits
- [RIR](../concepts/rir-relative-effort.md) - Affects recovery time
- [Mesocycle Structure](./mesocycle-structure.md) - Frequency across meso
