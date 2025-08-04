from systemd import journal
import select
from typing import Final


class JournalReader:
    """A class to read entries from the systemd journal for a specific unit."""
    LOG_DEBUG: Final = 7
    LOG_ERR: Final = 3
    LOG_INFO: Final = 6
    LOG_NOTICE: Final = 5
    LOG_WARNING: Final = 4

    def __init__(self, unit):
        try:
            self.unit = unit
            self.j = journal.Reader()
            self.j.log_level(journal.LOG_DEBUG)
            self.j.add_match(_SYSTEMD_UNIT=unit)
            self.j.seek_tail()
            self.j.get_previous()

            self.p = select.poll()
            self.p.register(self.j, self.j.get_events())

        except Exception as ex:
            self.log(
                f"Failed creating journal reader. Error was:{ex}", level=self.logger.LOG_ERR)

    def poll_read(self):
        entries = []
        while self.p.poll():
            if self.j.process() == journal.APPEND:
                for entry in self.j:
                    if entry['MESSAGE'] != "":
                        entries.append(entry['MESSAGE'])
                break
        return entries

    def log(self, message, level=LOG_INFO):
        """Log a message to the journal."""
        journal.send(message, _SYSTEMD_UNIT=self.unit, PRIORITY=level)

    def close(self):
        """Close the journal reader"""
        self.p.unregister(self.j)
        self.j.close()

    def __enter__(self):
        """Enter the runtime context related to this object."""
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """Exit the runtime context and close the journal reader."""
        self.close()
