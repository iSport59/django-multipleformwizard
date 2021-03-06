__version__ = '0.2.16'

try:
    # This is in a try-except block to prevent import errors at install time
    from .views import (SessionMultipleFormWizardView, CookieMultipleFormWizardView,
                        NamedUrlSessionMultipleFormWizardView, NamedUrlCookieMultipleFormWizardView,
                        MultipleFormWizardView, NamedUrlMultipleFormWizardView)
except ImportError:
    pass
