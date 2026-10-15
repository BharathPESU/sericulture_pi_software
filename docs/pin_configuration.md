1. Fixed Raspberry Pi 5 pin configuration
Physical pin no.	Pin type / BCM number	Component connected to that pin
1	3.3V power	SHT40 temperature and humidity sensor — VCC
3	GPIO2 — I²C SDA	SHT40 — SDA
5	GPIO3 — I²C SCL	SHT40 — SCL
6	GND	SHT40 — GND
9	GND	Float switch signal return
15	GPIO22 — Digital input	Magnetic float switch — water-level status
14	GND	Relay control ground, only as specified by the relay board's wiring instructions
29	GPIO5 — Digital output	Relay CH1 — Fan
31	GPIO6 — Digital output	Relay CH2 — Mist sprayer
33	GPIO13 — Digital output	Relay CH3 — Night light
36	GPIO16 — Digital output	Relay CH4 — Reserved
35	GPIO19 — Digital output	Relay CH5 — Reserved
38	GPIO20 — Digital output	Relay CH6 — Reserved
32	GPIO12 — Digital output	Active buzzer — control signal through a suitable driver if required
17	3.3V power	Buzzer module power, if compatible with its exact specification
34	GND	Buzzer/driver ground
37	GPIO26 — Digital input	Mains electricity status detector — isolated logic output
39	GND	Mains detector logic return, if using a compatible isolated dry-contact circuit
The SHT40 is an I²C device with a 1.08–3.6V supply range, so 3.3V is appropriate for the bare sensor. Confirm your breakout board's requirements before wiring it. 

sensirion.com



2. Components that do not use GPIO pins
Component	Connection to Raspberry Pi 5
Official Raspberry Pi AI HAT+ 26 TOPS	PCIe ribbon cable and GPIO stacking header
Arducam OV2710 USB UVC camera	USB-A port
Raspberry Pi Active Cooler	Dedicated fan connector on the Pi 5
5-inch HDMI display, if included	Micro-HDMI port; power connection depends on the display
The AI HAT+ uses the PCIe connection for AI acceleration. Keep physical pins 27 and 28 (GPIO0 and GPIO1) reserved for HAT identification; do not assign them to your application. 

Raspberry Pi Documentation
+1



3. Relay channel assignments — keep these fixed
Relay channel	GPIO	Physical pin	Load
CH1	GPIO5	29	Fan
CH2	GPIO6	31	Mist sprayer
CH3	GPIO13	33	Night light
CH4	GPIO16	36	Spare
CH5	GPIO19	35	Spare
CH6	GPIO20	38	Spare
For this particular Waveshare relay model, the board uses a separate 7–36V DC supply. Do not assume it can be powered from the Pi's 5V rail. Also, because the AI HAT+ occupies the GPIO header area, plan a labelled GPIO wiring harness rather than assuming both boards can be stacked directly without mechanical interference. 

Waveshare Wiki
+1



4. Important requirements before freezing the wiring
- Safe boot: all load circuits must default to OFF. Use the relay board's normally-open contacts where appropriate, and verify the relay's active-high/active-low behavior before connecting the actual loads.
- Mains monitoring: GPIO26 must receive a safe, isolated logic signal from an AC-presence detector. Never connect mains voltage directly to a Raspberry Pi pin.
- Float switch: GPIO22 can read its contact state using a suitable pull-up configuration. A simple two-wire switch cannot always distinguish a disconnected wire from a legitimate switch state; supervised wiring is needed if that distinction is required.
- Component fault detection: USB camera detection, I²C sensor detection, and Hailo detection can be checked in software. Relay GPIO commands alone cannot prove that a relay or fan physically works; add feedback hardware if the system must detect those failures.
- Buzzer alerts: an active buzzer can produce different beep patterns (short, long, repeated). If you require different pitches, use a passive buzzer instead.
My recommendation: freeze this table as your software pin map, but verify the exact relay-board model, logic polarity, power wiring, and buzzer driver before treating the wiring as final. The GPIO assignments need not change when the software is updated, provided the hardware remains the same.