from apify_client import ApifyClient

# Initialize the ApifyClient with your API token
client = ApifyClient("apify_api_0fhtifAMQ2AflH8JR3DhKnfT5rEdaN0kEYqb")

# Prepare the Actor input
run_input = {
    "hashtags": ["fyp"],
    "resultsPerPage": 100,
    "profiles": None,
    "profileScrapeSections": ["videos"],
    "profileSorting": "latest",
    "excludePinnedPosts": False,
    "oldestPostDateUnified": None,
    "newestPostDate": None,
    "mostDiggs": None,
    "leastDiggs": None,
    "maxFollowersPerProfile": 0,
    "maxFollowingPerProfile": 0,
    "searchQueries": None,
    "searchSection": "",
    "maxProfilesPerQuery": 10,
    "videoSearchSorting": "MOST_RELEVANT",
    "videoSearchDateFilter": "ALL_TIME",
    "postURLs": None,
    "scrapeRelatedVideos": False,
    "shouldDownloadVideos": False,
    "shouldDownloadCovers": False,
    "shouldDownloadSlideshowImages": False,
    "shouldDownloadAvatars": False,
    "shouldDownloadMusicCovers": False,
    "videoKvStoreIdOrName": None,
    "downloadSubtitlesOptions": "NEVER_DOWNLOAD_SUBTITLES",
    "commentsPerPost": 0,
    "topLevelCommentsPerPost": 0,
    "maxRepliesPerComment": 0,
    "proxyCountryCode": "None",
}

# Run the Actor and wait for it to finish
run = client.actor("GdWCkxBtKWOsKjdch").call(run_input=run_input)

# Fetch and print Actor results from the run's dataset (if there are any)
for item in client.dataset(run["defaultDatasetId"]).iterate_items():
    print(item)