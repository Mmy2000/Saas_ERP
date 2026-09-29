# Arabic UI with Western digits and "." decimals (OQ-13 default), matching legacy printouts
# and what staff type on keyboards. Django's stock "ar" formats use "," as the decimal mark.
DATE_FORMAT = "j F Y"
SHORT_DATE_FORMAT = "d/m/Y"
DATETIME_FORMAT = "j F Y، H:i"
SHORT_DATETIME_FORMAT = "d/m/Y H:i"
TIME_FORMAT = "H:i"
MONTH_DAY_FORMAT = "j F"
YEAR_MONTH_FORMAT = "F Y"
FIRST_DAY_OF_WEEK = 6  # Saturday
DECIMAL_SEPARATOR = "."
THOUSAND_SEPARATOR = ","
NUMBER_GROUPING = 3
