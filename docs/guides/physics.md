# Physics mode: a real grid behind the points

In simulation mode the points trace sine waves. In scenario mode they follow a
script. Physics mode goes further: it puts a working model of a power grid behind
the points and solves it. The numbers on the screen are no longer decoration, they
are the output of a power-flow calculation, and they respond to operator and
attacker actions the way a real grid does. Open a breaker and the flow does not
just vanish from one point. It redistributes across the rest of the network, and if
that overloads another line, that line trips too, and the failure cascades.

This is what turns a demo from "a number changed" into "I opened one breaker and
watched an overload ripple across three substations until the lights went out."
That is the moment that makes an attack on a control system feel real, and it is
exactly what training and showcases need.

## Run it

```bash
python3 suite/tase2ctl.py run grid-demo
```

That starts the server, the HMI bridge, and the co-simulation over the regional
grid model. Open <http://127.0.0.1:8800>. Every station shows live values that come
from the grid solver: generator MW and MVAR, line flows and reactive power, bus
voltages, system frequency, transformer loading and oil temperature, tie schedules
and Area Control Error. The grid sits in a stable steady state.

Open a breaker from the HMI (it is a select-before-operate control, so select then
operate) and the flow redistributes across the rest of the network on the next
solve. The regional grid is built to ride that through, so to watch a full
cascading blackout unfold one line at a time, run the scripted `cascade-demo`, which
opens a breaker on a grid deliberately tuned to tip into a cascade:

```bash
python3 suite/tase2ctl.py run cascade-demo
```

There the tie opens and its flow drops to zero, the parallel path goes over its
limit and trips, and the load island loses its source and collapses, those stations
go offline and their voltages read not-valid. The sequence plays out a couple of
seconds apart, so you can follow the cascade across the screen instead of everything
failing at once.

## How it works

Each tick the engine solves a DC power flow over the grid model
(`config/grid_utility.json` for `grid-demo`, `config/grid.json` for the smaller
tuned grid behind `cascade-demo`).
DC power flow is the standard fast approximation used across the industry: it models
real power, line reactance, and bus voltage angles, and from the angles it computes
the flow on every line. It then checks each line against its limit. If a line is
over its limit, it trips, the engine re-solves, and on the next tick it may trip the
next one. Buses that lose every path back to the generation are treated as blacked
out, which is how an island forms.

The engine also reads the breaker control objects, so a command from the HMI (or
from an attack, or from a scenario) feeds straight back into the model. It publishes
every result to the ICCP points over the same real protocol path as everything else,
so the traffic is genuine and capturable, and it works with the dataset and scoring
tools just like the other modes.

A note on honesty: voltage magnitude is an approximation here. DC power flow models
real power and angles, not voltage, so the bus voltages are a reasonable display
proxy derived from the angles, not a full AC solution. The line flows and the
cascade behaviour are the real physics.

## The grid model

`config/grid.json` describes the network in plain terms:

- **buses** are the nodes, one marked as the slack (the reference that balances
  generation and load),
- **generators** and **loads** put power in and take it out at buses,
- **lines** connect buses, each with a reactance and a thermal limit in MW,
- **breakers** map a control point to a line, so an operator command switches it,
- **measurements** map a physical quantity (a line flow, a bus voltage, a
  transformer thermal value) onto a published point by name, the same join key the
  tag database uses.

To model your own grid, edit those lists. Validate it against the point model
first:

```bash
python3 suite/physics.py validate --grid config/grid.json --config config/scada.json
```

The validator checks that buses and lines line up, that there is exactly one slack,
and that every breaker and measurement names a real line, bus, and point. The
smaller `config/grid.json` behind `cascade-demo` is deliberately tuned so the
network is comfortably stable at rest but a single tie opening tips it into a
cascade, which makes the point in one click; the regional `config/grid_utility.json`
behind `grid-demo` is built to stay stable. Change the limits and the loads and you
change where, and whether, a grid cascades.

## Why this is more than a nicer demo

Once the grid is physical, attacks have physical consequences you can measure. A
false-data injection that hides a real overload, an unauthorized command that opens
the wrong breaker, a sequence designed to trip a specific line: these stop being
abstract protocol events and become outcomes you can see, capture, label, and score.
It is also the foundation for the kind of detection that signatures alone cannot do,
where you catch an attack not by its bytes but by the impossible or dangerous grid
state it produces.
