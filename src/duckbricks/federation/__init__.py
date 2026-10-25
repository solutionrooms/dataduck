"""Federation with a Databricks workspace: connections, live foreign catalogs and the mirror.

See ADR 0013. Reads go straight to the workspace's storage with credentials vended by Unity
Catalog; the SQL warehouse is used only for views.
"""
