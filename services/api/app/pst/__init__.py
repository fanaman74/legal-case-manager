"""Outlook mailboxes (.pst and .ost): reading them, indexing every email for
search, and turning chosen emails into .eml files to add to the case.

PST files come from outside, so like every converter they are read in a
child process of the worker (which has no internet access); a damaged
mailbox can only stop that child process."""
