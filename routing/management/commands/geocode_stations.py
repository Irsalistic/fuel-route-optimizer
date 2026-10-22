from django.core.management.base import BaseCommand

from routing.services.stations import rebuild_station_file


class Command(BaseCommand):
    help = "Place each truck stop at its city centroid and write data/stations.json."

    def handle(self, *args, **options):
        stats = rebuild_station_file()
        self.stdout.write(
            f"Wrote {stats['stations']} stations to {stats['path']}. "
            f"Skipped {stats['skipped_non_us']} non-US rows. "
            f"Unmatched cities: {stats['unmatched']}."
        )
        if stats["unmatched_names"]:
            preview = ", ".join(stats["unmatched_names"][:40])
            self.stdout.write(preview)
