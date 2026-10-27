# TODO

- make an exhaustive pass to find all metrics that return values?

## Notes / known limitations

- Energy lifetime totals (v0.13.0): the counter is built by folding the
  this-month accumulator, so it can miss energy used between the last stats poll
  of a month and the midnight-on-the-1st reset — a few minutes at the 10-min
  poll. Acceptable; tightening it would mean trusting the rolling monthly
  (idx 4) series, which replies intermittently and rolls off.