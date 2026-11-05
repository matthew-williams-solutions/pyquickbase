from pyquickbase import ConfigurationError

try:
    qb.app("not an app ID")
except ConfigurationError as error:
    print(error)
