# tempkeeper

A small Python package for keeping and summarizing temperature readings:
convert between Celsius and Fahrenheit (`tempkeeper.convert`), load readings
from a CSV file (`tempkeeper.io.load_readings`), and collect them in a
`ReadingStore` that can report the median (`tempkeeper.store`).
