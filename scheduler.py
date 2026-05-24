from backend.tasks.maintenance import deactivate_expired_campaigns, delete_rejected_clips


def run_scheduler():
    deactivate_expired_campaigns()
    delete_rejected_clips()


if __name__ == "__main__":
    run_scheduler()
