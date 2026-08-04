"""The one place that knows which stores exist.

Everything else derives what it needs from here, or from the database:

  fetching   reads this file          (Flipp's merchant label cannot be guessed)
  importing  derives the directory    (it is the store key)
  scheduling derives the steps        (they follow from `source`)
  the site   derives its store list   (from what is actually published)

Before this, the same store was declared in four files under two naming
conventions — `hmart` in three of them, `H Mart` in the fourth. The database
inherited both: pipeline_runs stores slugs, batches stores display names, and the
admin dashboard joined one to the other. That join silently returned nothing, so
the two stores that failed every week showed no run history at all. Four months
of outage were invisible for exactly this reason.

Adding a store that an existing source already covers is one entry here.
"""

STORES = {
    "safeway": {
        "display_name": "Safeway",
        "source": "flipp",
        "flipp_merchant": "Safeway",
        "enabled": True,
    },
    "cardenas": {
        "display_name": "Cardenas",
        "source": "flipp",
        # Flipp's label, which is not what this project calls the store. Get this
        # wrong and the imports land under a second store name that never
        # supersedes the first, leaving both published forever.
        "flipp_merchant": "Cardenas Markets",
        "enabled": True,
    },
    "hmart": {
        "display_name": "H Mart",
        "source": "flipp",
        "flipp_merchant": "H Mart",
        "enabled": True,
    },
    "99ranch": {
        "display_name": "99 Ranch",
        "source": "ranch99",     # not carried by Flipp; has its own JSON API
        "store_id": 8899,        # Mountain View
        "enabled": True,
    },
}

# Steps per source, not per store. A new store on an existing source needs no
# entry here; a new *kind* of source does.
PIPELINE_STEPS = {
    "flipp":   ["download_flipp_flyer.py {key}", "import_to_database.py {key}"],
    "ranch99": ["download_99ranch_flyer.py", "parse_99ranch_deals.py",
                "import_to_database.py {key}"],
}


def enabled_stores():
    return {k: v for k, v in STORES.items() if v.get("enabled", True)}


def display_name(key):
    """Slug -> display name. Unknown keys pass through, so a stray key is visible
    in output rather than raising deep inside a run."""
    entry = STORES.get(key)
    return entry["display_name"] if entry else key


def key_for_display_name(name):
    """Display name -> slug. The direction the admin dashboard needs."""
    for key, entry in STORES.items():
        if entry["display_name"] == name:
            return key
    return None


def steps_for(key):
    entry = STORES[key]
    return [step.format(key=key) for step in PIPELINE_STEPS[entry["source"]]]
