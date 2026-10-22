class LocationError(Exception):
    """Start or end could not be resolved to a US city."""


class RouteNotFound(Exception):
    """The router has no driving path between the two points."""


class RoutingServiceError(Exception):
    """The external routing service failed or timed out."""


class Unreachable(Exception):
    """No sequence of truck stops keeps the truck inside its range."""
