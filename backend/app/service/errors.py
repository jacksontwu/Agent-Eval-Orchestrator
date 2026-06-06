class ServiceError(Exception):
    status_code = 400


class NotFoundError(ServiceError):
    status_code = 404


class ConflictError(ServiceError):
    status_code = 409
