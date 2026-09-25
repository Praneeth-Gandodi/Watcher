# Presenting Watcher

A guide for explaining this system to judges, and for demonstrating it, in
terms you can actually stand up and deliver. Nothing here requires you to know
how to code.

You do not need to be able to implement any of this to present it well. You need
to be able to say **what problem it solves**, **how it works in one sentence per
idea**, **why the interface is shaped the way it is**, and **what you measured**.
Those are the four things judges are grading, and all four are learnable.

There is an honest framing for the fact that you did not write the code, at the
end of this document. Read that section before the demo, not during.

---

## 1. The sixty-second version

Say this early, in roughly this shape, and then stop and let them ask questions.

> "A warehouse with 500 robots has a problem: somebody has to decide who does
> what. The usual answer is a central computer that assigns every job and
> watches every robot. That's also the bottleneck and the single point of
> failure — if it goes down, the whole floor stops.
>
> We took the opposite approach. There is no central controller. Each robot
> bids for its own work, like freelancers on a job marketplace. The cheapest bid
> wins. The winner plans its own route, yields its own right of way, and hands
> its task off if it runs low on battery or fails.
>
> The result is that when we deliberately kill the coordination service, the
> floor keeps working. Robots just claim the jobs themselves. That's the thing
> I'd like to show you."

That is the whole pitch. Everything after this is depth on demand.

**Why this framing wins:** it states a real problem, names a real industry
approach, takes a position against it, and ends on a demonstrable claim rather
than a feature list. It also hands the judges a hook — "show me that" — which
you want.

---

## 2. The problem, in plain language

Do not start with the technology. Start with the warehouse.

**The situation.** Big warehouses are being asked to run more robots on the same
floor. Amazon-style fulfilment centres now run hundreds of mobile robots moving
shelves and totes.

**The obvious solution, and why it's the wrong one.** Put one computer in charge.
It knows where every robot is and what every robot should do next. Every robot
listens to it.

That works right up until three things happen:

1. **The single point of failure.** The one computer is now the most expensive
   failure in the building. If it has a bad second, a thousand robots freeze.
2. **The bottleneck.** Every decision — 500 robots, many times a second — goes
   through one place. You pay for the compute, and you add latency to every
   single movement.
3. **The coverage problem.** The central computer has to track every robot
   perfectly. Radio drops, sensor errors, and network lag all become fleet-wide
   bugs, because every decision depends on the central view being right.

**The insight we used.** If the robots could coordinate *with each other*, the
central computer would stop being necessary — and the failure, the bottleneck,
and the coverage problem all go away at the same time. That is a real trade,
not a free lunch: peer coordination is more expensive per robot and needs good
local safety rules. It is worth it here.

---

## 3. What we built, in plain language

Think of it as a marketplace on a factory floor.

**A job arrives.** Something needs collecting from aisle 12.

**Robots bid.** Every robot that is free, has the right equipment, and has enough
battery looks at the job and works out what it would cost *it* to do it — mostly
distance, plus a penalty for being low on battery, plus how busy it already is.
No robot knows about any other robot's bid. They just each work it out locally.

**The cheapest bid wins.** Not by a boss choosing — the bid is just the lowest
number, and everyone can verify that. The work goes to the robot that said the
lowest thing.

**The winner plans its own route.** It looks at the floor plan, the racks it must
go around, the cells other robots have already claimed, and the no-go zones, and
finds its own way there. It does not wait for permission.

**Along the way it manages conflicts.** Two robots heading into the same aisle:
the system predicts the collision a second and a half before it happens, picks
which one gives way using a written-down priority order, and the one that gives
way replans around the problem. The one with right of way never has to slow down.

**It handles its own failures.** Battery low: hand the job to a robot that can
finish it, go charge. Robot dead: its job is migrated to another robot. Two
robots stuck waiting on each other: the cycle is detected and broken. Radio goes
quiet: the robot is marked unreachable and the fleet stops making assumptions
about where it is.

**Every decision is logged as an event.** This is what makes the dashboard honest
— it is not a simulation of a dashboard, it is a view over the real event stream.

---

## 4. The five ideas, one sentence each

If a judge asks "so how does it actually work", these are your five answers.

| Idea | One sentence | Why it matters |
|---|---|---|
| **Peer bidding** | Robots bid for jobs themselves; the lowest cost wins, and anyone can verify it. | Removes the central controller from the critical path. |
| **Soft occupancy** | A cell another robot has claimed is expensive to enter, not forbidden. | One robot parking in an aisle can never seal it. The fleet cannot deadlock itself by parking. |
| **Predict, then yield** | Each robot predicts the next 1.5 seconds of its own path and checks for conflicts before they happen. | Right-of-way is decided once, calmly, in advance — not by two robots colliding and sorting it out. |
| **Wait-for cycles** | We track which robot is waiting on which, and when that graph contains a loop, a deadlock exists. | Deadlock is detected, named, and broken deliberately rather than discovered as a stuck robot. |
| **Energy as a scheduling input** | A robot that cannot finish its job hands it to one that can, before it strands itself. | Battery is a coordination problem, not just a hardware one. |

The soft-occupancy one is worth dwelling on if you get a technical judge. It is
the single decision that makes decentralized coordination viable in a dense space,
and it is the opposite of the naive approach.

---

## 5. The dashboard, panel by panel

This is what you are looking at, and what to say about each part. The design
decisions are the part judges notice, because most teams ship a working system
with an ugly interface and yours has to be visibly considered.

### The top bar

Left: what this is. Middle: five live numbers. Right: connection health,
coordinator status, search, refresh, theme.

> "The numbers across the top are the fleet's vital signs: how many robots,
> how much work is in hand, how much battery is left overall, how many
> conflicts are open, and how fast the system is producing decisions. Every one
> is read straight from the backend's own metrics. If a number isn't available
> we show a dash — we never invent one."

That last sentence is worth saying out loud. It signals engineering maturity.

### The map — this is the page, not a widget

The map is the centre of the layout because the whole problem is *spatial*. If
you can see 500 robots negotiating floor space at a glance, the system makes
sense without a single word of explanation. That is the core design decision:
the diagram organises the page rather than sitting in a box on it.

Say this about the markers:

> "Each robot is a different shape as well as a different colour. A circle is
> working, a triangle is yielding, a square is charging, a diamond can't be
> reached, a cross has failed. So even in greyscale, or for a colourblind
> viewer, or on a bad projector, the state is readable. Colour alone is never
> carrying the meaning."

That is a genuinely strong design point and most teams miss it entirely.

Say this about the routes:

> "Four hundred robots each have a route. Drawn at full strength that's an
> unreadable mess. So the whole fleet's movement is a faint background flow, and
> the moment you select one robot, its route is traced brightly on top with its
> destination marked. Overview when you want it, detail the instant you ask."

### The right rail — inspector and controls

> "Selecting a robot shows everything about it: battery, workload, position,
> radio state, its current route and its assigned task. From here you can inject
> a fault — fail this robot, cut off its radio, or restore it — and watch the
> system recover. That's how we demonstrate resilience rather than just claiming
> it."

### The bottom dock — six tabs

> "Roster is all 500 robots, searchable and filterable. Tasks is the work queue.
> Events is the live event stream — the system's actual decisions as they
> happen. Bids shows what robots are bidding and for how much. Safety shows
> conflicts, deadlocks and the recovery actions being taken. Performance shows
> our own measured timings."

The Bids tab is your proof that the "marketplace" claim is real. Open it during
the demo if a judge is technical — you can see the cost breakdown per bid.

### The design decisions, if asked

- **Why two themes?** Control rooms are dim. Meeting rooms are bright. Both are
  first-class, and we follow your operating system's preference on first load
  rather than picking one for you.
- **Why does it stay smooth with 500 robots?** The floor and the routes are drawn
  once into an off-screen layer and reused; only the robots are redrawn each
  frame. The roster renders only the rows that fit on screen. Events are batched
  so a burst of them can't cause a burst of screen redraws.
- **Why the empty states?** When there's no data, it says so. A dashboard that
  invents numbers when the backend is down is worse than one that admits it.
- **Accessibility.** Status is carried by shape and label as well as colour, and
  the system respects your "reduce motion" setting — the map's animations stop.

---

## 6. The live demo

Eight minutes. Practise it until you can do it without thinking about it.

**Before you start:** set the speed to `2×` or `4×`. Travel is visible in
seconds rather than minutes, and it makes the floor look alive. You can do this
in front of them as your first action — it also shows the controls work.

### Minute 0–1 — the hook

> "Five hundred robots, one warehouse, no central controller. Watch the top
> right — that's the coordinator. I'm going to show you what happens when it
> goes away."

Set speed to 2×. Let them see the floor moving.

### Minute 1–3 — the fleet

- Let them watch. Don't talk over it. The map moving is the most persuasive
  thing you have.
- Point out: "Those aren't dots on a timer. Every one of them decided for itself
  to be here."
- Point at the top bar: "Five hundred robots, four hundred of them holding
  work right now."

### Minute 3–5 — one robot, in detail

This is the moment that turns "cool animation" into "real system".

- Press `Ctrl+K`, type `robot-0100`, press Enter.
- You now have that robot's full telemetry in the right rail.
- Say: "That's one robot. It won an auction for a job, planned its own route,
  and yielded its own right of way three times on the way there."

If you want to go further, click **Fail**. Its job immediately migrates to
another robot and the fleet absorbs it. Say: "I just killed it. Nobody is
supervising that handover — the task is reassigned by the same bidding
mechanism."

### Minute 5–7 — the payoff

Go to the **Safety** tab. Then wait for the coordinator to drop.

> "The coordinator is about to go down. This is on a schedule we control — there
> is no command in our contract to trigger it, so we didn't invent one."

When the top-right indicator flips to *Coordinator down*:

> "The coordinator is gone. Nothing on this floor is supervised. And the work
> keeps being assigned — the robots just claim the jobs themselves now. Watch
> the task count. It's still climbing."

Point at the metrics: completed tasks going up *during* the outage. This is the
single most convincing thirty seconds of the demo.

### Minute 7–8 — the evidence

Go to the **Performance** tab.

> "We don't ask you to take our word for the scale. Here is what each part of
> the simulation costs, measured live. A tick at 500 robots is about 90
> milliseconds, and the simulation runs at 5 hertz, so it holds real time."

Then stop. Let them ask.

### If the coordinator has already cycled past

Don't wait and don't look anxious. Just say: "It's on a repeating schedule —
ninety seconds. We can trigger the next one from here," and open a task in
`Ctrl+K` so the crowd has something to look at. Or simply move to the
Performance tab and come back to it if it fires.

---

## 7. Numbers you can quote

Only quote these if asked. Say where each came from.

| Claim | Number | Where it comes from |
|---|---|---|
| Fleet size | 500 robots | Configurable; that is what the demo runs |
| Decisions per second | ~220 events/sec at 500 robots | The event rate tile, live |
| Tick cost at 500 robots | ~90 ms mean, against a 200 ms budget | The performance tab, live |
| Real time held at 500 robots | 2–8% of deadlines missed, 0% at 50 and 200 | Repeatable test in the repo |
| Tasks completed | hundreds per minute at 500 robots | The metrics tile, live |
| Route efficiency | ~1.2× the straight-line distance | Means it takes a sensible path, not a random one |
| Deadlocks | detected and broken, continuously | Safety tab, live |
| Tests | 336 backend, 100 frontend | `pytest` and `npm test` |

The most useful one to have ready: **"about 1.2 times the straight-line
distance"** for route efficiency. It answers "are the robots actually going
sensibly, or wandering" in one number, and the answer is good.

---

## 8. Questions judges actually ask

### "What's the point? Real warehouses do this already."

> "They do, with a central controller. Our claim isn't that central is wrong —
> it's that it's a single point of failure and a scaling bottleneck, and that
> peer coordination is a viable alternative. We built the smallest honest version
> of that alternative and measured it. What we have is not production
> warehouse software; it's evidence the approach works and an honest account of
> where it stops working."

Never claim you have replaced production warehouse systems. You will be asked
about safety certification, real hardware, and latency guarantees, and you will
lose credibility if you have implied otherwise.

### "How is this different from just scripting robots along fixed paths?"

> "Nobody has a fixed path. Every route is planned at the moment the robot gets
> the job, against the current state of the floor — including which cells other
> robots have claimed. If a robot dies, the route is replanned. If two robots
> conflict, one of them reroutes. There's no script."

### "Isn't a central controller more reliable?"

This is the best question you can get. Take it seriously.

> "For assigning work, probably — and our coordinator is still there doing that
> when it's up. Where it isn't more reliable is the failure case. Our
> coordinator going down degrades the system; a central controller going down
> stops it. And the cost profile is different: our approach is more expensive per
> robot in compute, which is why the tick rate drops to 5 hertz above 200 robots.
> That's a real trade-off we made deliberately and documented."

Admitting the trade-off is what makes the rest of your claims believable.

### "What's the bottleneck? Where does it break next?"

> "At higher density. The floor grows with the fleet to keep robots from being
> closer together than they are wide, because at that point every pair trips the
> safety margin and conflict detection saturates. Push past that and you'd need
> hierarchical coordination — local zones negotiating, zones negotiating with
> each other. That's the natural next step and we haven't built it."

### "What's the technology?"

> "Python and FastAPI on the backend, React and TypeScript on the front. The
> important part isn't the stack, it's that all the communication — what a robot
> looks like, what events exist, what commands you can send — is defined in one
> frozen contract that everything validates against. The simulation and the
> dashboard both have to obey it, so they can't drift apart."

### "How do you know the dashboard isn't making things up?"

> "Every number traces to a field in the backend's projection, and the
> dashboard rejects any payload that doesn't validate rather than trusting it. The
> event stream is the backend's actual decisions. The one place the interface
> derives something is the deadlock overlay — the projection doesn't publish
> active deadlocks, so that panel infers them from the event stream and we
> documented that as a limitation instead of hiding it."

### "Is this real-time hardware or simulation?"

> "Pure software simulation. Every robot is a state machine — no physics, no
> actuators, no sensor noise. The coordination logic is real and the decisions
> are genuinely made, but the robots aren't. We compressed robot speeds above
> real warehouse robots so travel is visible in a demo, and that's documented in
> the README."

### "Did you write all of this?"

See the next section.

---

## 9. The honest framing of your role

You should not claim to have written code you didn't write. But you should not
be vague about what you *do* own either — judges can tell the difference between
someone who understands their system and someone who has read a README.

**What you genuinely own, and should present as yours:**

- **The problem framing.** Why this problem matters and why the obvious
  solution is the wrong one. That's a design judgement.
- **The demo.** Which moment you show, in what order, and what you say about it.
  That is the part that decides whether anyone understands your system.
- **The interface direction.** Why the map is the page, why there are two themes,
  why status is carried by shape as well as colour, why there's no fake data.
- **The integration.** The backend and the console had to agree on a contract,
  and the deployed thing is one working system rather than two demos.
- **The judgement about what not to claim.** Knowing the limits of your own
  system is a senior skill and it's visible in how you answer questions.

**If asked directly, something like:**

> "The system was built by a small team with me on the product and integration
> side — the problem framing, the interface direction, and the demo. I didn't
> write the simulation internals, and I'll be straight with you about which
> parts I can go deep on and which I can't. What I can speak to in depth is why
> it's built this way, what it does, and where it breaks."

That answer is strong, not weak. It signals self-awareness, and a judge who catches
you overclaiming will trust nothing else you say.

**If pressed on a specific implementation detail you don't know** — say so, and
offer to find out:

> "I don't know that off the top of my head. I can check the code and come back
> to you."

Never guess at an implementation detail. In a technical judging panel, one
confident wrong answer costs you more than three honest ones.

---

## 10. Traps

Things that will cost you the demo if you say them.

- **Don't say "it scales to any number of robots."** It doesn't. Density breaks
  first, and you know where.
- **Don't say "it's real-time."** Say it holds real time at 500 robots and drops
  to 5 hertz, and quote the number.
- **Don't say "it's production ready" or "it could run in a warehouse."** It's a
  simulation. Say so before they ask.
- **Don't say "AI" or "machine learning."** It's an auction algorithm with good
  engineering around it. Overclaiming here is the fastest way to lose a
  technical judge.
- **Don't claim the dashboard is "real-time accurate."** It refreshes on a timer
  and patches between snapshots. Say "live".
- **Don't apologise for the simulation.** It's a legitimate, deliberate choice
  for this problem. "Software-only simulation" is a feature, not a shortfall.
- **Don't read the README to them.** Use it to check facts, then talk in your
  own words.
- **Don't demo at 1× speed.** Nothing appears to happen. Set 2× or 4× first.

---

## 11. The night before

- [ ] Read section 1 out loud five times until you could say it with the lights off.
- [ ] Run the demo twice end to end, out loud, with a timer.
- [ ] Confirm the deployed URL loads and the fleet size is 500.
- [ ] Know your three numbers: 90 ms tick, 1.2× route efficiency, 0–8% missed deadlines.
- [ ] Re-read section 9 so your role answer is ready, not improvised.
- [ ] Have `docs/deployment.md` open in another tab in case they ask how it's hosted.
- [ ] If a laptop is used, test the projector or shared screen. The dark theme is
      the default and it projects well; check the light theme doesn't wash out.

You know this system well enough. The gap is vocabulary, and this document is the
vocabulary. Go and win it.
