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
python3 suite/tase2ctl.py run physics-demo
```

That starts the server, the HMI bridge, and the co-simulation. Open
<http://127.0.0.1:8800>. Every station shows live values that come from the grid
solver: tie-line flow, feeder flows, bus voltages, transformer loading and oil
temperature. The grid sits in a stable steady state.

Now cause a cascade. Open the main tie breaker, `plc1_brk`, from the HMI (it is a
select-before-operate control, so select then operate). Watch what happens over the
next few seconds:

1. The tie opens, so its flow drops to zero.
2. The parallel feeder now has to carry that power. It goes over its limit and
   trips.
3. With both paths gone, the load island loses its source and collapses. Those
   stations go offline and their voltages read not-valid.

The whole sequence plays out one line at a time, a couple of seconds apart, so you
can follow the cascade across the screen instead of everything failing at once.

## How it works

Each tick the engine solves a DC power flow over the model in `config/grid.json`.
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
and that every breaker and measurement names a real line, bus, and point. The demo
grid is deliberately tuned so the network is comfortably stable at rest but a single
tie opening tips it into a cascade, which makes the point in one click. Change the
limits and the loads and you change where, and whether, it cascades.

## Why this is more than a nicer demo

Once the grid is physical, attacks have physical consequences you can measure. A
false-data injection that hides a real overload, an unauthorized command that opens
the wrong breaker, a sequence designed to trip a specific line: these stop being
abstract protocol events and become outcomes you can see, capture, label, and score.
It is also the foundation for the kind of detection that signatures alone cannot do,
where you catch an attack not by its bytes but by the impossible or dangerous grid
state it produces.
