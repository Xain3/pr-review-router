- src/pager.py: the literal 100 becomes a module-private _PAGE_SIZE = 100 that page() slices with, so the page length is unchanged and the name is not part of the public interface.

Overall this looks fine.
