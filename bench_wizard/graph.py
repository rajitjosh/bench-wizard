class CycleError(ValueError):
    def __init__(self, names):
        super().__init__("dependency cycle between: " + ", ".join(names))
        self.names = list(names)


def toposort(names, requires):
    """Order names so each follows everything it requires; ties keep input order.

    Requirements that are not in `names` are ignored.
    """
    names = list(names)
    known = set(names)
    done, out = set(), []
    while len(out) < len(names):
        for name in names:
            if name in done:
                continue
            if all(r in done or r not in known or r == name for r in requires.get(name, ())):
                done.add(name)
                out.append(name)
                break
        else:
            raise CycleError([n for n in names if n not in done])
    return out
