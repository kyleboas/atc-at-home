# Real KSFO (ADS-B) vs the simulator

Real: adsb.fi heatmap, 12 days (2026-01-03, 2026-02-11, 2026-07-15, 2026-07-16, 2026-11-06, 2026-11-07, 2026-11-08, 2026-11-09, 2026-11-10, 2026-11-11, 2026-11-12, 2026-11-13), 6680 landings and 6472 departures detected; half hours per configuration: west 105.5 h, west_28s 44.0 h, southeast 40.0 h, west_28r 25.0 h, west_28r_28s 7.0 h, east 5.5 h, west_28l 1.5 h.
Sim: seeds 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18 with the current controller.py, resampled to 30 s. Landings found on sim tracks against the sim's own record: {'landed, found': 371, 'arrivals not landed by the end of the run': 114, 'landed, other runway': 5, 'landed, not found': 1}.

Throughput rows are hourly rates of each half hour (sim: sliding 30 min windows, 5 min steps). The sim's rates are capped by the traffic it is given (arrivals/h, departures/h per level: light 18-22 / 16-20, medium 28-32 / 24-28, peak 40-44 / 34-38), so a lower sim rate is a gap only if its arrivals are being delayed. Real spacing mixes all wake pairs (the heatmap has no aircraft type).

## Gaps worth an experiment

- **west**: of 182 sim arrivals, 126 landed, 35 diverted and 21 were still airborne when the run stopped.
- **west_28s**: of 63 sim arrivals, 57 landed, 1 diverted and 5 were still airborne when the run stopped.
- **west_28r**: of 59 sim arrivals, 56 landed, 0 diverted and 3 were still airborne when the run stopped.
- **west_28l**: of 63 sim arrivals, 60 landed, 0 diverted and 3 were still airborne when the run stopped.
- **southeast**: of 62 sim arrivals, 43 landed, 5 diverted and 14 were still airborne when the run stopped.
- **east**: of 62 sim arrivals, 35 landed, 12 diverted and 15 were still airborne when the run stopped.
- **west**: median distance at which arrivals join final is 13.4 in the sim vs 8.8 real (sim higher).
- **west**: busy-period landing rate (p90 of half hours) is 27/h in the sim vs 48/h real (the sim meters arrivals to an AAR of 36/h).
- **west**: busy-period departure rate (p90 of half hours) is 26/h in the sim vs 46/h real.
- **west_28s**: median in-trail spacing at the threshold is 6.4 in the sim vs 7.0 real (sim lower).
- **west_28s**: median time between landings on one runway is 158.6 in the sim vs 175.6 real (sim lower).
- **west_28s**: busy-period landing rate (p90 of half hours) is 24/h in the sim vs 41/h real (the sim meters arrivals to an AAR of 36/h).
- **west_28s**: busy-period departure rate (p90 of half hours) is 18/h in the sim vs 38/h real.
- **west_28r**: median time between departures off one runway is 164.1 in the sim vs 123.0 real (sim higher).
- **west_28r**: median track miles from the ring is 20.6 in the sim vs 25.0 real (sim lower).
- **west_28r**: median minutes from the ring is 6.9 in the sim vs 8.4 real (sim lower).
- **west_28r**: busy-period landing rate (p90 of half hours) is 22/h in the sim vs 26/h real (the sim meters arrivals to an AAR of 30/h).
- **west_28r**: busy-period departure rate (p90 of half hours) is 24/h in the sim vs 32/h real.
- **west_28l**: median time between departures off one runway is 151.8 in the sim vs 108.2 real (sim higher).
- **west_28l**: median distance at which arrivals join final is 11.8 in the sim vs 13.8 real (sim lower).
- **southeast**: median in-trail spacing at the threshold is 5.8 in the sim vs 4.5 real (sim higher).
- **southeast**: median time between landings on one runway is 138.5 in the sim vs 121.9 real (sim higher).
- **southeast**: median distance at which arrivals join final is 14.8 in the sim vs 11.9 real (sim higher).
- **southeast**: median track miles from the ring is 22.6 in the sim vs 46.6 real (sim lower).
- **southeast**: median minutes from the ring is 7.2 in the sim vs 13.4 real (sim lower).
- **southeast**: median ground speed 4 nm out is 154.2 in the sim vs 140.0 real (sim higher).
- **southeast**: busy-period landing rate (p90 of half hours) is 16/h in the sim vs 30/h real (the sim meters arrivals to an AAR of 30/h).
- **southeast**: busy-period departure rate (p90 of half hours) is 28/h in the sim vs 34/h real.
- **east**: median in-trail spacing at the threshold is 6.2 in the sim vs 4.7 real (sim higher).
- **east**: median time between landings on one runway is 151.3 in the sim vs 127.4 real (sim higher).
- **east**: median time between departures off one runway is 79.7 in the sim vs 113.4 real (sim lower).
- **east**: median track miles from the ring is 19.2 in the sim vs 46.3 real (sim lower).
- **east**: median minutes from the ring is 6.8 in the sim vs 14.9 real (sim lower).
- **east**: median ground speed 4 nm out is 152.4 in the sim vs 133.3 real (sim higher).
- **east**: busy-period landing rate (p90 of half hours) is 15/h in the sim vs 28/h real (the sim meters arrivals to an AAR of 30/h).
- **east**: busy-period departure rate (p90 of half hours) is 32/h in the sim vs 24/h real.

## west

| metric | real | sim |
|---|---|---|
| In-trail spacing at the leader's threshold crossing (same runway) (nm, p10 / med) | 4.6 / 5.9 (n=2902, min 2.9) | 3.8 / 5.8 (n=59, min 2.7) |
| Time between landings on the same runway (pairs <= 4 min) (s, p10 / med) | 115 / 145 (n=2902, min 72.6) | 94 / 142 (n=59, min 66.4) |
| 28L/28R: time to the nearest landing on the other parallel (s, p10 / med) | 14 / 38 (n=3432, min 0.1) | 53 / 120 (n=100, min 6.3) |
| Landings per hour (each half hour) (/h, med / p90) | 34 / 48 (n=211, max 54) | 20 / 27 (n=57, max 28) |
| Departures per hour (each half hour) (/h, med / p90) | 34 / 46 (n=211, max 58) | 22 / 26 (n=57, max 30) |
| Movements per hour (each half hour) (/h, med / p90) | 68 / 88 (n=211, max 102) | 44 / 46 (n=57, max 50) |
| Time between departures off the same runway (<= 4 min) (s, p10 / med) | 83 / 137 (n=2119, min 26.1) | 74 / 122 (n=44, min 70.0) |
| Time between any two departures (<= 4 min) (s, p10 / med) | 18 / 90 (n=3180, min 0.3) | 74 / 127 (n=106, min 11.9) |
| Joins final at (distance to threshold) (nm, p10 / med / p90) | 4.2 / 8.8 / 13.4 (n=3636) | 9 / 13 / 26 (n=134) |
| Altitude when joining final (ft, p10 / med / p90) | 1300 / 2475 / 3962 (n=3636) | 2700 / 3450 / 7921 (n=134) |
| Intercept angle onto final (deg, p10 / med / p90) | 6 / 24 / 60 (n=3636) | 25 / 36 / 47 (n=134) |
| Downwind lateral offset from the final course (nm, p10 / med / p90) | 0.7 / 2.5 / 9.8 (n=1022) | 5.6 / 5.9 / 7.0 (n=39) |
| Downwind extends to (distance out along the final course) (nm, p10 / med / p90) | 10.6 / 13.1 / 15.8 (n=1022) | 14.3 / 15.4 / 16.5 (n=39) |
| Track miles from the 20 nm ring to the threshold (nm, p10 / med / p90) | 18 / 20 / 54 (n=3644) | 18 / 20 / 59 (n=134) |
| Minutes from the 20 nm ring to the threshold (min, p10 / med / p90) | 5.9 / 6.8 / 14.4 (n=3644) | 6.0 / 6.7 / 16.7 (n=134) |
| Altitude crossing the 20 nm ring (ft, p10 / med / p90) | 5733 / 6100 / 13561 (n=3644) | 5100 / 5947 / 12000 (n=134) |
| Ground speed 4 nm out (kt, p10 / med / p90) | 140 / 155 / 170 (n=3650) | 142 / 152 / 158 (n=134) |
| Ground speed 1 nm out (kt, p10 / med / p90) | 121 / 137 / 152 (n=3125) | 131 / 142 / 150 (n=107) |

## west_28s

| metric | real | sim |
|---|---|---|
| In-trail spacing at the leader's threshold crossing (same runway) (nm, p10 / med) | 5.0 / 7.0 (n=1222, min 2.7) | 3.8 / 6.4 (n=24, min 3.0) |
| Time between landings on the same runway (pairs <= 4 min) (s, p10 / med) | 128 / 176 (n=1222, min 63.9) | 95 / 159 (n=24, min 78.5) |
| 28L/28R: time to the nearest landing on the other parallel (s, p10 / med) | 31 / 58 (n=1420, min 1.9) | 79 / 108 (n=44, min 49.4) |
| Landings per hour (each half hour) (/h, med / p90) | 34 / 41 (n=88, max 48) | 20 / 24 (n=21, max 24) |
| Departures per hour (each half hour) (/h, med / p90) | 27 / 38 (n=88, max 44) | 12.0 / 18.0 (n=21, max 22) |
| Movements per hour (each half hour) (/h, med / p90) | 60 / 72 (n=88, max 78) | 32 / 36 (n=21, max 38) |
| Time between departures off the same runway (<= 4 min) (s, p10 / med) | 51 / 137 (n=758, min 19.1) | 117 / 134 (n=13, min 116.3) |
| Time between any two departures (<= 4 min) (s, p10 / med) | 46 / 97 (n=1057, min 6.2) | 63 / 125 (n=20, min 37.8) |
| Joins final at (distance to threshold) (nm, p10 / med / p90) | 5.4 / 12.3 / 18.9 (n=1410) | 9 / 14 / 20 (n=60) |
| Altitude when joining final (ft, p10 / med / p90) | 2175 / 3650 / 5900 (n=1410) | 2700 / 3350 / 5932 (n=60) |
| Intercept angle onto final (deg, p10 / med / p90) | 8 / 35 / 82 (n=1410) | 30 / 33 / 47 (n=60) |
| Downwind lateral offset from the final course (nm, p10 / med / p90) | 0.6 / 1.8 / 9.5 (n=523) | 5.6 / 6.0 / 6.9 (n=28) |
| Downwind extends to (distance out along the final course) (nm, p10 / med / p90) | 9.7 / 13.1 / 15.5 (n=523) | 13.9 / 15.9 / 16.8 (n=28) |
| Track miles from the 20 nm ring to the threshold (nm, p10 / med / p90) | 18 / 22 / 60 (n=1482) | 18 / 20 / 62 (n=60) |
| Minutes from the 20 nm ring to the threshold (min, p10 / med / p90) | 6.1 / 7.8 / 16.6 (n=1482) | 6.1 / 6.6 / 17.2 (n=60) |
| Altitude crossing the 20 nm ring (ft, p10 / med / p90) | 5833 / 6488 / 13884 (n=1482) | 4970 / 6041 / 12100 (n=60) |
| Ground speed 4 nm out (kt, p10 / med / p90) | 126 / 143 / 169 (n=1488) | 148 / 153 / 157 (n=60) |
| Ground speed 1 nm out (kt, p10 / med / p90) | 122 / 137 / 153 (n=1299) | 131 / 140 / 147 (n=49) |

## west_28r

| metric | real | sim |
|---|---|---|
| In-trail spacing at the leader's threshold crossing (same runway) (nm, p10 / med) | 3.9 / 5.5 (n=273, min 3.0) | 3.4 / 5.4 (n=43, min 1.0) |
| Time between landings on the same runway (pairs <= 4 min) (s, p10 / med) | 99 / 135 (n=273, min 73.8) | 87 / 136 (n=43, min 24.9) |
| 28L/28R: time to the nearest landing on the other parallel (s, p10 / med) | 39 / 157 (n=27, min 23.3) | - |
| Landings per hour (each half hour) (/h, med / p90) | 14 / 26 (n=50, max 30) | 20 / 22 (n=21, max 24) |
| Departures per hour (each half hour) (/h, med / p90) | 22 / 32 (n=50, max 52) | 18 / 24 (n=21, max 26) |
| Movements per hour (each half hour) (/h, med / p90) | 34 / 56 (n=50, max 80) | 38 / 44 (n=21, max 46) |
| Time between departures off the same runway (<= 4 min) (s, p10 / med) | 75 / 123 (n=286, min 37.4) | 74 / 164 (n=16, min 69.5) |
| Time between any two departures (<= 4 min) (s, p10 / med) | 36 / 99 (n=434, min 11.6) | 72 / 113 (n=33, min 13.2) |
| Joins final at (distance to threshold) (nm, p10 / med / p90) | 5.1 / 11.6 / 15.2 (n=369) | 3.2 / 11.3 / 14.5 (n=57) |
| Altitude when joining final (ft, p10 / med / p90) | 1545 / 3300 / 4650 (n=369) | 1087 / 3000 / 4090 (n=57) |
| Intercept angle onto final (deg, p10 / med / p90) | 8 / 34 / 74 (n=369) | 18 / 30 / 47 (n=57) |
| Downwind lateral offset from the final course (nm, p10 / med / p90) | 0.4 / 1.5 / 7.4 (n=104) | 5.6 / 5.9 / 6.8 (n=17) |
| Downwind extends to (distance out along the final course) (nm, p10 / med / p90) | 10.6 / 12.9 / 15.0 (n=104) | 13.3 / 14.4 / 16.8 (n=17) |
| Track miles from the 20 nm ring to the threshold (nm, p10 / med / p90) | 18 / 25 / 55 (n=376) | 19 / 21 / 60 (n=57) |
| Minutes from the 20 nm ring to the threshold (min, p10 / med / p90) | 6.0 / 8.4 / 14.4 (n=376) | 6.1 / 6.9 / 17.4 (n=57) |
| Altitude crossing the 20 nm ring (ft, p10 / med / p90) | 5623 / 6166 / 13498 (n=376) | 5570 / 6075 / 12000 (n=57) |
| Ground speed 4 nm out (kt, p10 / med / p90) | 141 / 156 / 172 (n=376) | 137 / 152 / 158 (n=57) |
| Ground speed 1 nm out (kt, p10 / med / p90) | 127 / 141 / 153 (n=320) | 131 / 137 / 147 (n=51) |

## west_28l

| metric | real | sim |
|---|---|---|
| In-trail spacing at the leader's threshold crossing (same runway) (nm, p10 / med) | 5.6 / 6.7 (n=2, min 5.4) | 3.8 / 4.9 (n=43, min 3.5) |
| Time between landings on the same runway (pairs <= 4 min) (s, p10 / med) | 137 / 162 (n=2, min 130.3) | 100 / 126 (n=43, min 87.9) |
| 28L/28R: time to the nearest landing on the other parallel (s, p10 / med) | 192 / 192 (n=3, min 191.6) | 80 / 111 (n=4, min 79.6) |
| Landings per hour (each half hour) (/h, med / p90) | 8.0 / 11.2 (n=3, max 12) | 20 / 24 (n=20, max 24) |
| Departures per hour (each half hour) (/h, med / p90) | 22 / 27 (n=3, max 28) | 19 / 24 (n=20, max 28) |
| Movements per hour (each half hour) (/h, med / p90) | 30 / 38 (n=3, max 40) | 40 / 44 (n=20, max 46) |
| Time between departures off the same runway (<= 4 min) (s, p10 / med) | 92 / 108 (n=21, min 80.7) | 91 / 152 (n=22, min 68.7) |
| Time between any two departures (<= 4 min) (s, p10 / med) | 65 / 104 (n=25, min 36.4) | 72 / 127 (n=44, min 21.1) |
| Joins final at (distance to threshold) (nm, p10 / med / p90) | 10.4 / 13.8 / 19.9 (n=13) | 9 / 12 / 23 (n=60) |
| Altitude when joining final (ft, p10 / med / p90) | 2975 / 4100 / 5795 (n=13) | 2790 / 3122 / 6451 (n=60) |
| Intercept angle onto final (deg, p10 / med / p90) | 30 / 47 / 58 (n=13) | 30 / 46 / 47 (n=60) |
| Downwind lateral offset from the final course (nm, p10 / med / p90) | 4.4 / 4.4 / 4.4 (n=1) | 5.7 / 6.0 / 6.9 (n=17) |
| Downwind extends to (distance out along the final course) (nm, p10 / med / p90) | 9.7 / 9.7 / 9.7 (n=1) | 13.3 / 15.4 / 16.5 (n=17) |
| Track miles from the 20 nm ring to the threshold (nm, p10 / med / p90) | 18 / 19 / 40 (n=13) | 19 / 20 / 66 (n=60) |
| Minutes from the 20 nm ring to the threshold (min, p10 / med / p90) | 6.0 / 6.4 / 11.8 (n=13) | 6.1 / 6.7 / 18.0 (n=60) |
| Altitude crossing the 20 nm ring (ft, p10 / med / p90) | 5557 / 5686 / 9455 (n=13) | 5789 / 6071 / 12000 (n=60) |
| Ground speed 4 nm out (kt, p10 / med / p90) | 143 / 150 / 161 (n=13) | 139 / 153 / 158 (n=60) |
| Ground speed 1 nm out (kt, p10 / med / p90) | 134 / 145 / 154 (n=11) | 131 / 137 / 147 (n=51) |

## southeast

| metric | real | sim |
|---|---|---|
| In-trail spacing at the leader's threshold crossing (same runway) (nm, p10 / med) | 3.6 / 4.5 (n=853, min 2.5) | 4.1 / 5.8 (n=29, min 1.5) |
| Time between landings on the same runway (pairs <= 4 min) (s, p10 / med) | 95 / 122 (n=853, min 66.0) | 102 / 139 (n=29, min 33.9) |
| Landings per hour (each half hour) (/h, med / p90) | 26 / 30 (n=80, max 32) | 13.0 / 16.0 (n=22, max 16) |
| Departures per hour (each half hour) (/h, med / p90) | 24 / 34 (n=80, max 42) | 20 / 28 (n=22, max 30) |
| Movements per hour (each half hour) (/h, med / p90) | 50 / 62 (n=80, max 68) | 32 / 42 (n=22, max 46) |
| Time between departures off the same runway (<= 4 min) (s, p10 / med) | 83 / 125 (n=565, min 26.0) | 71 / 112 (n=27, min 67.4) |
| Time between any two departures (<= 4 min) (s, p10 / med) | 69 / 109 (n=823, min 10.7) | 70 / 117 (n=47, min 67.4) |
| Joins final at (distance to threshold) (nm, p10 / med / p90) | 10.2 / 11.9 / 15.0 (n=921) | 13.9 / 14.8 / 17.3 (n=47) |
| Altitude when joining final (ft, p10 / med / p90) | 3075 / 3325 / 4950 (n=921) | 3700 / 3900 / 4919 (n=47) |
| Intercept angle onto final (deg, p10 / med / p90) | 32 / 53 / 91 (n=921) | 30 / 30 / 75 (n=47) |
| Downwind lateral offset from the final course (nm, p10 / med / p90) | 4.6 / 6.4 / 9.8 (n=555) | 7.1 / 7.9 / 8.1 (n=33) |
| Downwind extends to (distance out along the final course) (nm, p10 / med / p90) | 10.3 / 12.2 / 14.4 (n=555) | 15.7 / 16.4 / 17.1 (n=33) |
| Track miles from the 20 nm ring to the threshold (nm, p10 / med / p90) | 21 / 47 / 59 (n=918) | 19 / 23 / 81 (n=47) |
| Minutes from the 20 nm ring to the threshold (min, p10 / med / p90) | 7.6 / 13.4 / 17.7 (n=918) | 7 / 7 / 23 (n=47) |
| Altitude crossing the 20 nm ring (ft, p10 / med / p90) | 6075 / 8100 / 10694 (n=918) | 3960 / 7270 / 11000 (n=47) |
| Ground speed 4 nm out (kt, p10 / med / p90) | 126 / 140 / 154 (n=926) | 149 / 154 / 159 (n=47) |
| Ground speed 1 nm out (kt, p10 / med / p90) | 121 / 136 / 149 (n=801) | 131 / 140 / 150 (n=39) |

## east

| metric | real | sim |
|---|---|---|
| In-trail spacing at the leader's threshold crossing (same runway) (nm, p10 / med) | 4.0 / 4.7 (n=82, min 3.3) | 2.1 / 6.2 (n=18, min 0.1) |
| Time between landings on the same runway (pairs <= 4 min) (s, p10 / med) | 100 / 127 (n=82, min 89.7) | 52 / 151 (n=18, min 1.6) |
| Landings per hour (each half hour) (/h, med / p90) | 18 / 28 (n=11, max 30) | 10.0 / 15.2 (n=25, max 16) |
| Departures per hour (each half hour) (/h, med / p90) | 16 / 24 (n=11, max 28) | 10 / 32 (n=25, max 38) |
| Movements per hour (each half hour) (/h, med / p90) | 38 / 46 (n=11, max 48) | 20 / 48 (n=25, max 54) |
| Time between departures off the same runway (<= 4 min) (s, p10 / med) | 90 / 113 (n=63, min 55.8) | 70 / 80 (n=47, min 67.6) |
| Time between any two departures (<= 4 min) (s, p10 / med) | 89 / 113 (n=66, min 55.8) | 70 / 79 (n=48, min 67.6) |
| Joins final at (distance to threshold) (nm, p10 / med / p90) | 11.5 / 12.2 / 15.5 (n=92) | 13 / 14 / 23 (n=38) |
| Altitude when joining final (ft, p10 / med / p90) | 3250 / 3750 / 4225 (n=92) | 3500 / 4450 / 6487 (n=38) |
| Intercept angle onto final (deg, p10 / med / p90) | 18 / 38 / 100 (n=92) | 30 / 30 / 47 (n=38) |
| Downwind lateral offset from the final course (nm, p10 / med / p90) | 4.6 / 7.2 / 11.1 (n=85) | 8.0 / 15.8 / 16.6 (n=29) |
| Downwind extends to (distance out along the final course) (nm, p10 / med / p90) | 10.8 / 14.0 / 16.1 (n=85) | 12.2 / 13.2 / 16.1 (n=29) |
| Track miles from the 20 nm ring to the threshold (nm, p10 / med / p90) | 27 / 46 / 53 (n=92) | 18 / 19 / 32 (n=38) |
| Minutes from the 20 nm ring to the threshold (min, p10 / med / p90) | 9.4 / 14.9 / 17.6 (n=92) | 6.4 / 6.8 / 9.8 (n=38) |
| Altitude crossing the 20 nm ring (ft, p10 / med / p90) | 6226 / 7395 / 9550 (n=92) | 4833 / 5400 / 7004 (n=38) |
| Ground speed 4 nm out (kt, p10 / med / p90) | 120 / 133 / 142 (n=92) | 149 / 152 / 157 (n=38) |
| Ground speed 1 nm out (kt, p10 / med / p90) | 120 / 134 / 143 (n=83) | 130 / 140 / 147 (n=33) |

## west_28r_28s

| metric | real | sim |
|---|---|---|
| In-trail spacing at the leader's threshold crossing (same runway) (nm, p10 / med) | 5.1 / 5.7 (n=41, min 4.6) | - |
| Time between landings on the same runway (pairs <= 4 min) (s, p10 / med) | 132 / 148 (n=41, min 125.5) | - |
| 28L/28R: time to the nearest landing on the other parallel (s, p10 / med) | 61 / 78 (n=5, min 61.0) | - |
| Landings per hour (each half hour) (/h, med / p90) | 8.0 / 17.4 (n=14, max 24) | - |
| Departures per hour (each half hour) (/h, med / p90) | 14 / 32 (n=14, max 36) | - |
| Movements per hour (each half hour) (/h, med / p90) | 23 / 48 (n=14, max 52) | - |
| Time between departures off the same runway (<= 4 min) (s, p10 / med) | 74 / 115 (n=65, min 44.6) | - |
| Time between any two departures (<= 4 min) (s, p10 / med) | 54 / 106 (n=90, min 41.6) | - |
| Joins final at (distance to threshold) (nm, p10 / med / p90) | 9.0 / 13.5 / 15.0 (n=70) | - |
| Altitude when joining final (ft, p10 / med / p90) | 2890 / 3838 / 4628 (n=70) | - |
| Intercept angle onto final (deg, p10 / med / p90) | 24 / 24 / 67 (n=70) | - |
| Downwind lateral offset from the final course (nm, p10 / med / p90) | 0.3 / 1.2 / 4.9 (n=16) | - |
| Downwind extends to (distance out along the final course) (nm, p10 / med / p90) | 11.3 / 13.6 / 15.5 (n=16) | - |
| Track miles from the 20 nm ring to the threshold (nm, p10 / med / p90) | 18 / 20 / 55 (n=71) | - |
| Minutes from the 20 nm ring to the threshold (min, p10 / med / p90) | 6.3 / 7.3 / 15.7 (n=71) | - |
| Altitude crossing the 20 nm ring (ft, p10 / med / p90) | 5630 / 6273 / 13938 (n=71) | - |
| Ground speed 4 nm out (kt, p10 / med / p90) | 131 / 145 / 160 (n=71) | - |
| Ground speed 1 nm out (kt, p10 / med / p90) | 124 / 137 / 152 (n=61) | - |
