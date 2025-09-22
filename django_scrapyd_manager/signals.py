


from django.dispatch import Signal


__all__ = [
    "guard_obj_success",
    "guard_obj_error",
    "guard_objects_started",
    "guard_objects_ended",
]


guard_obj_success = Signal()                # payload: scheduler, model, logs
guard_obj_error = Signal()                  # payload: scheduler, model, exception
guard_objects_started = Signal()            # payload: scheduler, objects
guard_objects_ended = Signal()              # payload: scheduler, result
