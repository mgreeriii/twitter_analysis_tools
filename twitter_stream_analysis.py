# -*- coding: utf-8 -*-
from authentication_keys import get_account_credentials
from time_helpers import *
from process_text import *
from process_tweet_object import *
from graph_helper import *
from file_helpers import *

from nltk.stem.snowball import SnowballStemmer
from collections import Counter
from itertools import combinations
from twarc import Twarc
from tweepy import OAuthHandler
from tweepy import API
from tweepy import Cursor
import spacy
import numpy as np
import Queue
import threading
import sys
import time
import os
import io
import re

spacy_supported_langs = ["en", "de", "es", "pt", "fr", "it", "nl"]
lang_map = {"da": "danish",
            "nl": "dutch",
            "en": "english",
            "fi": "finnish",
            "fr": "french",
            "de": "german",
            "hu": "hungarian",
            "it": "italian",
            "no": "norwegian",
            "pt": "portuguese",
            "ro": "romanian",
            "ru": "russian",
            "es": "spanish",
            "sv": "swedish"}

##################
# Global variables
##################
stopping = False
debug = False
follow = False
search = False
tweet_queue = None
targets = []
to_follow = []
data = {}
conf = {}
stopwords = {}


##################
# Helper functions
##################
def debug_print(string):
    if debug == True:
        print string

def write_daily_data(name, output_fn, prefix, suffix, label):
    if name in data[day_label]:
        dirname = prefix + "daily/" + name
        if not os.path.exists(dirname):
            os.makedirs(dirname)
        filename = prefix + "daily/" + name + "/" + name + "_" + day_label + suffix
        output_fn(data[day_label][name], filename)

def write_hourly_data(name, output_fn, prefix, suffix, label):
    if name in data[hour_label]:
        dirname = prefix + "hourly/" + name
        if not os.path.exists(dirname):
            os.makedirs(dirname)
        filename = prefix + "hourly/" + name + "/" + name + "_" + hour_label + suffix
        output_fn(data[hour_label][name], filename)

def save_output(name, filetype):
    output_fn = None
    suffix = ""
    if "json" in filetype:
        output_fn = save_json
        prefix = "data/json/"
        suffix = ".json"
    elif "csv" in filetype:
        output_fn = save_counter_csv
        prefix = "data/"
        suffix = ".csv"
    elif "gephi" in filetype:
        output_fn = save_gephi_csv
        prefix = "data/"
        suffix = ".csv"

    if name in data:
        filename = prefix + "overall/" + name + suffix
        output_fn(data[name], filename)
    if search == False:
        write_daily_data(name, output_fn, prefix, suffix, day_label)
        write_hourly_data(name, output_fn, prefix, suffix, hour_label)
    else:
        day_labels = []
        hour_labels = []
        for key, vals in data.iteritems():
            m = re.search("^([0-9]+)$", key)
            if m is not None:
                captured = m.group(1)
                if len(captured) == 8:
                    day_labels.append(captured)
                else:
                    hour_labels.append(captured)
        for l in day_labels:
            write_daily_data(name, output_fn, prefix, suffix, l)
        for l in hour_labels:
            write_daily_data(name, output_fn, prefix, suffix, l)

def read_settings(filename):
    debug_print(sys._getframe().f_code.co_name)
    config = {}
    if os.path.exists(filename):
        with open(filename, "r") as file:
            for line in file:
                if line is not None:
                    line = line.strip()
                    if len(line) > 0:
                        name, value = line.split("=")
                        name = name.strip()
                        value = int(value)
                        if value == 1:
                            config[name] = True
                        elif value == 0:
                            config[name] = False
    return config

def read_config(filename, preserve_case=False):
    debug_print(sys._getframe().f_code.co_name)
    ret_array = []
    if os.path.exists(filename):
        with io.open(filename, "r", encoding="utf-8") as file:
            for line in file:
                line = line.strip()
                if preserve_case == False:
                    line = line.lower()
                ret_array.append(line)
    return ret_array

def get_active_threads():
    debug_print(sys._getframe().f_code.co_name)
    return len(threading.enumerate())

def cleanup():
    debug_print(sys._getframe().f_code.co_name)
    global dump_file_handle, volume_file_handle, tweet_file_handle, tweet_url_file_handle, stopping
    if get_active_threads > 2:
        print "Waiting for queue to empty..."
        stopping = True
    #    tweet_queue.join()
    tweet_file_handle.close()
    tweet_url_file_handle.close()
    dump_file_handle.close()
    volume_file_handle.close()
    print "Serializing data..."
    serialize()
    dump_data()
    dump_graphs()

def load_settings():
    debug_print(sys._getframe().f_code.co_name)
    global conf
    if "settings" not in conf:
        conf["settings"] = {}
    conf["settings"] = read_settings("config/settings.txt")

    params_files = ["monitored_hashtags", "targets", "follow", "search", "ignore", "keywords", "good_users", "bad_users", "url_keywords", "languages", "description_keywords", "legit_sources", "fake_news_sources"]
    for p in params_files:
        filename = "config/" + p + ".txt"
        conf[p] = read_config(filename)
    conf["params"] = {}
    conf["params"]["default_dump_interval"] = 10
    conf["params"]["serialization_interval"] = 10
    conf["params"]["graph_dump_interval"] = 10


##########
# Storage
##########
def check_for_counter(name):
    debug_print(sys._getframe().f_code.co_name)
    debug_print(name)
    global data
    if "counters" not in data:
        data["counters"] = {}
    if name not in data["counters"]:
        data["counters"][name] = 0

def increment_counter(name):
    debug_print(sys._getframe().f_code.co_name)
    debug_print(name)
    global data
    check_for_counter(name)
    data["counters"][name] += 1

def set_counter(name, value):
    debug_print(sys._getframe().f_code.co_name)
    global data
    check_for_counter(name)
    data["counters"][name] = value

def get_counter(name):
    debug_print(sys._getframe().f_code.co_name)
    check_for_counter(name)
    return data["counters"][name]

def get_all_counters():
    debug_print(sys._getframe().f_code.co_name)
    if "counters" in data:
        return data["counters"]

def record_list(label, item):
    debug_print(sys._getframe().f_code.co_name)
    global data
    if label not in data:
        data[label] = []
    if item not in data[label]:
        data[label].append(item)

def record_freq_dist(label, item, collect_periodic=True):
    debug_print(sys._getframe().f_code.co_name)
    global data
    if label not in data:
        data[label] = Counter()
    data[label][item] += 1

    if collect_periodic == True:
        if tweet_day_label not in data:
            data[tweet_day_label] = {}
        if label not in data[tweet_day_label]:
            data[tweet_day_label][label] = Counter()
        data[tweet_day_label][label][item] += 1

        if tweet_hour_label not in data:
            data[tweet_hour_label] = {}
        if label not in data[tweet_hour_label]:
            data[tweet_hour_label][label] = Counter()
        data[tweet_hour_label][label][item] += 1

def record_map(label, key, value, collect_periodic=True):
    debug_print(sys._getframe().f_code.co_name)
    global data
    if label not in data:
        data[label] = {}
    if key not in data[label]:
        data[label][key] = []
    if value not in data[label][key]:
        data[label][key].append(value)

    if collect_periodic == True:
        if tweet_day_label not in data:
            data[tweet_day_label] = {}
        if label not in data[tweet_day_label]:
            data[tweet_day_label][label] = {}
        if key not in data[tweet_day_label][label]:
            data[tweet_day_label][label][key] = []
        if value not in data[tweet_day_label][label][key]:
            data[tweet_day_label][label][key].append(value)

        if tweet_hour_label not in data:
            data[tweet_hour_label] = {}
        if label not in data[tweet_hour_label]:
            data[tweet_hour_label][label] = {}
        if key not in data[tweet_hour_label][label]:
            data[tweet_hour_label][label][key] = []
        if value not in data[hour_label][label][key]:
            data[tweet_hour_label][label][key].append(value)

def record_freq_dist_map(label, item1, item2, collect_periodic=True):
    debug_print(sys._getframe().f_code.co_name)
    global data
    if label not in data:
        data[label] = {}
    if item1 not in data[label]:
        data[label][item1] = Counter()
    data[label][item1][item2] += 1

    if collect_periodic == True:
        if tweet_day_label not in data:
            data[tweet_day_label] = {}
        if label not in data[tweet_day_label]:
            data[tweet_day_label][label] = {}
        if item1 not in data[tweet_day_label][label]:
            data[tweet_day_label][label][item1] = Counter()
        data[tweet_day_label][label][item1][item2] += 1

        if tweet_hour_label not in data:
            data[tweet_hour_label] = {}
        if label not in data[tweet_hour_label]:
            data[tweet_hour_label][label] = {}
        if item1 not in data[tweet_hour_label][label]:
            data[tweet_hour_label][label][item1] = Counter()
        data[tweet_hour_label][label][item1][item2] += 1

def record_interarrival(name, tweet_time):
    global data
    debug_print(sys._getframe().f_code.co_name)
    if "interarrivals" not in data:
        data["interarrivals"] = {}
    if name in data["interarrivals"]:
        inter = data["interarrivals"][name]
        if "previous_tweeted" in inter:
            delta = tweet_time - inter["previous_tweeted"]
            if delta > 0:
                if delta not in data["interarrivals"][name]:
                    data["interarrivals"][name][delta] = 1
                else:
                    data["interarrivals"][name][delta] += 1
    else:
        data["interarrivals"][name] = {}
    data["interarrivals"][name]["previous_tweeted"] = tweet_time

def calculate_interarrival_statistics(name):
    debug_print(sys._getframe().f_code.co_name)
    stdev = 0.0
    counts = []
    if "interarrivals" in data and name in data["interarrivals"]:
        inter = data["interarrivals"][name]
        for key, val in inter.iteritems():
            if key != "previous_tweeted":
                counts.append(val)
        if len(counts) > 0:
            stdev = float(np.std(counts))
    return stdev

def get_network_params():
    debug_print(sys._getframe().f_code.co_name)
    edges = 0
    nodes = 0
    if "user_user_map" in data:
        edges = sum([len(x) for x in data["user_user_map"].values()])
        nodeset = set()
        for source, targets in data["user_user_map"].iteritems():
            nodeset.add(source)
            for target, value in targets.iteritems():
                nodeset.add(target)
        nodes = len(nodeset)
    return nodes, edges




######################
# Follow functionality
######################
def get_account_data_for_names(names):
    print("Got " + str(len(names)) + " names.")
    auth = OAuthHandler(consumer_key, consumer_secret)
    auth.set_access_token(access_token, access_token_secret)
    auth_api = API(auth)

    batch_len = 100
    batches = (names[i:i+batch_len] for i in range(0, len(names), batch_len))
    ret = []
    for batch_count, batch in enumerate(batches):
        sys.stdout.write("#")
        sys.stdout.flush()
        users_list = auth_api.lookup_users(screen_names=batch)
        users_json = (map(lambda t: t._json, users_list))
        ret += users_json
    return ret

def get_ids_from_names(names):
    ret = []
    all_json = get_account_data_for_names(names)
    for d in all_json:
        if "id_str" in d:
            id_str = d["id_str"]
            ret.append(id_str)
    return ret






#############
# Dump text
#############
def dump_counters():
    debug_print(sys._getframe().f_code.co_name)
    counter_dump = get_all_counters()
    val_output = ""
    date_output = ""
    if counter_dump is not None:
        for n, c in sorted(counter_dump.iteritems()):
            val = None
            if type(c) is float:
                val = "%.2f"%c
                val_output += unicode(val) + u"\t" + unicode(n) + u"\n"
            elif len(str(c)) > 9:
                val = unix_time_to_readable(int(c))
                date_output += unicode(val) + u"\t" + unicode(n) + u"\n"
            else:
                val = c
                val_output += unicode(val) + u"\t" + unicode(n) + u"\n"
    handle = io.open("data/_counters.txt", "w", encoding='utf-8')
    handle.write(unicode(val_output))
    handle.write(u"\n")
    handle.write(unicode(date_output))
    handle.close







#############
# Dump graphs
#############
def record_volume_data(category, label, timestamp, value):
    debug_print(sys._getframe().f_code.co_name)
    global data
    if category not in data:
        data[category] = {}
    if label not in data[category]:
        data[category][label] = []
    data[category][label].append([timestamp, value])

def get_volume_labels(category):
    debug_print(sys._getframe().f_code.co_name)
    global data
    ret = []
    if category in data:
        for label, stuff in data[category].iteritems():
            ret.append(label)
    return ret

def get_volume_data(category, label):
    debug_print(sys._getframe().f_code.co_name)
    global data
    ret = {}
    if category in data:
        if label in data[category]:
            ret = data[category][label]
    return ret

def dump_tweet_volume_graphs():
    debug_print(sys._getframe().f_code.co_name)
    labels = get_volume_labels("tweet_volumes")
    for l in labels:
        volume_data = get_volume_data("tweet_volumes", l)
        if len(volume_data) > 5:
            dates = []
            volumes = []
            for item in volume_data:
                dates.append(item[0])
                volumes.append(item[1])
            chart_data = {}
            chart_data["tweets/sec"] = volumes
            dirname = "data/"
            filename = "_tweet_volumes_" + l + ".svg"
            title = "Tweet Volumes (" + l + ")"
            dump_line_chart(dirname, filename, title, dates, chart_data)

def dump_languages_graphs():
    debug_print(sys._getframe().f_code.co_name)
    counter_data = get_all_counters()
    prefixes = ["tweets", "captured_tweets"]
    for p in prefixes:
        if counter_data is not None:
            chart_data = {}
            for name, value in sorted(counter_data.iteritems(), key=lambda x:x[1], reverse= True):
                m = re.search("^" + p + "_([a-z][a-z][a-z]?)$", name)
                if m is not None:
                    item = m.group(1)
                    chart_data[item] = value
            dirname = "data/"
            filename = "_" + p + "_lang_breakdown.svg"
            title = "Language breakdown"
            dump_pie_chart(dirname, filename, title, chart_data)

def create_all_graphs(name):
    debug_print(sys._getframe().f_code.co_name)
    for x, y in {"hour": "hourly", "day": "daily"}.iteritems():
        x_labels = []
        all_items = []
        for s in range(10):
            label = get_datestring(x, s)
            x_labels.append(label)
            if label in data:
                if name in data[label]:
                    title = name + " " + label
                    dataset = dict(data[label][name].most_common(15))
                    if len(dataset) > 0:
                        dirname = "data/graphs/" + y + "/" + name + "/pie/"
                        filename = name + "_" + label + ".svg"
                        dump_pie_chart(dirname, filename, title, dataset)
                    for n, v in data[label][name].most_common(5):
                        if n not in all_items:
                            all_items.append(n)
        chart_data = {}
        for item in all_items:
            chart_data[item] = []
        for s in list(reversed(range(10))):
            label = get_datestring(x, s)
            dataset = {}
            if label in data and name in data[label]:
                dataset = dict(data[label][name].most_common(10))
            for item in all_items:
                if item in dataset.keys():
                    chart_data[item].append(dataset[item])
                else:
                    chart_data[item].append(0)

        dirname = "data/graphs/" + y + "/" + name + "/bar/"
        if x == "hour":
            filename = name + "_" + hour_label + ".svg"
        else:
            filename = name + "_" + day_label + ".svg"
        x_labels = list(reversed(x_labels))
        dump_bar_chart(dirname, filename, name, x_labels, chart_data)



########################
# Periodically dump data
########################

def serialize():
    debug_print(sys._getframe().f_code.co_name)
    filename = "data/raw/serialized.json"
    save_json(data, filename)
    filename = "data/raw/conf.json"
    save_json(conf, filename)

    custom = ["interarrivals", "description_matches", "keyword_matches", "hashtag_matches", "url_matches", "interarrival_matches", "interacted_with_bad"]
    for n in custom:
        if n in data:
            filename = "data/custom/" + n + ".json"
            save_json(data[n], filename)

    if "who_tweeted_what" in data:
        filename = "data/raw/who_tweeted_what.json"
        save_json(data["who_tweeted_what"], filename)

    jsons = ["all_hashtags", "all_mentioned", "user_user_map", "user_hashtag_map", "tag_map", "word_frequencies", "all_urls", "urls_not_twitter", "fake_news_urls", "fake_news_tweeters"]
    for n in jsons:
        save_output(n, "json")

    gephis = ["user_user_map", "user_hashtag_map"]
    for n in gephis:
        save_output(n, "gephi")

    return

def dump_data():
    debug_print(sys._getframe().f_code.co_name)
    dump_counters()
    dump_languages_graphs()
    dump_tweet_volume_graphs()

    csvs = ["all_hashtags", "all_mentioned", "word_frequencies", "all_urls", "urls_not_twitter", "fake_news_urls", "fake_news_tweeters"]
    for n in csvs:
        save_output(n, "csv")

    return

def dump_graphs():
    debug_print(sys._getframe().f_code.co_name)
    graphs = ["all_hashtags", "all_mentioned", "word_frequencies"]
    for g in graphs:
        create_all_graphs(g)
    return

def dump_event():
    debug_print(sys._getframe().f_code.co_name)
    if search == True:
        return
    if stopping == True:
        return
    global data, volume_file_handle, day_label, hour_label
    output = ""

# Dump text files
    interval = get_counter("dump_interval")
    prev_dump = get_counter("previous_dump_time")
    start_time = int(time.time())
    if start_time > prev_dump + interval:
        dump_data()
        dump_time = int(time.time()) - start_time
        output += "Data dump took: " + str(dump_time) + " seconds.\n"

# Dump graphs
        interval = get_counter("graph_dump_interval")
        prev_dump = get_counter("previous_graph_dump_time")
        start_time = int(time.time())
        if start_time > prev_dump + interval:
            dump_graphs()
            set_counter("previous_graph_dump_time", int(time.time()))
            dump_time = int(time.time()) - start_time
            output += "Graph dump took: " + str(dump_time) + " seconds.\n"

# Serialize
        interval = get_counter("serialization_interval")
        prev_dump = get_counter("previous_serialize")
        start_time = int(time.time())
        if start_time > prev_dump + interval:
            set_counter("previous_serialize", int(time.time()))
            serialize()
            dump_time = int(time.time()) - start_time
            output += "Serialization took: " + str(dump_time) + " seconds.\n"

        current_time = int(time.time())
        processing_time = current_time - get_counter("previous_dump_time")

        queue_length = tweet_queue.qsize()
        output += str(queue_length) + " items in the queue.\n"

        tweets_seen = get_counter("tweets_processed_this_interval")
        output += "Processed " + str(tweets_seen) + " tweets during the last " + str(processing_time) + " seconds.\n"
        tweets_captured = get_counter("tweets_captured_this_interval")
        output += "Captured " + str(tweets_captured) + " tweets during the last " + str(processing_time) + " seconds.\n"

        output += "Tweets encountered: " + str(get_counter("tweets_encountered")) + ", captured: " + str(get_counter("tweets_captured")) + ", processed: " + str(get_counter("tweets_processed")) + "\n"

        tpps = float(float(get_counter("tweets_processed_this_interval"))/float(processing_time))
        set_counter("tweets_per_second_processed_this_interval", tpps)
        output += "Processed/sec: " + str("%.2f" % tpps) + "\n"

        tcps = float(float(get_counter("tweets_captured_this_interval"))/float(processing_time))
        set_counter("tweets_per_second_captured_this_interval", tcps)
        output += "Captured/sec: " + str("%.2f" % tcps) + "\n"

        nodes, edges = get_network_params()
        output += "Nodes: " + str(nodes) + " Edges: " + str(edges) + "\n"

        set_counter("tweets_processed_this_interval", 0)
        set_counter("tweets_captured_this_interval", 0)
        set_counter("processing_time", processing_time)
        increment_counter("successful_loops")
        output += "Executed " + str(get_counter("successful_loops")) + " successful loops.\n"

        total_running_time = int(time.time()) - get_counter("script_start_time")
        set_counter("total_running_time", total_running_time)
        output += "Running as " + acct_name + " since " + script_start_time_str + " (" + str(total_running_time) + " seconds)\n"

        current_time_str = time.strftime("%Y-%m-%d %H:%M:%S")
        output += "Current time is: " + current_time_str + "\n\n"

        set_counter("average_tweets_per_second", tcps)
        set_counter("previous_dump_time", int(time.time()))
        print
        print output

# Record tweet volumes
        record_volume_data("tweet_volumes", "all_tweets", current_time_str, tcps)
        volume_file_handle.write(current_time_str + "\t" + str("%.2f" % tcps) + "\n")

# Reload config
        load_settings()
        filename = "data/raw/conf.json"
        save_json(conf, filename)

# Update timestamp labels and delete old data
        day_label = get_datestring("day")
        if day_label not in data:
            data[day_label] = {}
        hour_label = get_datestring("hour")
        if hour_label not in data:
            data[hour_label] = {}
        for offset in range(10, 100):
            offset_label = get_datestring("day", offset)
            if offset_label in data:
                del(data[offset_label])
            offset_label = get_datestring("hour", offset)
            if offset_label in data:
                del(data[offset_label])
        return









###############
# Process tweet
###############
def process_tweet(status):
    global data, tweet_hour_label, tweet_day_label
    debug_print(sys._getframe().f_code.co_name)
# If the tweet doesn't contain a user object or "text" it's useless
    if "user" not in status:
        increment_counter("faulty_tweets")
        debug_print("Faulty tweet")
        return
    user = status["user"]
    if "screen_name" not in user:
        increment_counter("faulty_tweets")
        debug_print("Faulty tweet")
        return
    text = get_text(status)
    if text is None:
        increment_counter("faulty_tweets")
        debug_print("Faulty tweet")
        return
    if "created_at" not in status:
        increment_counter("faulty_tweets")
        debug_print("Faulty tweet")
        return
# At this point, we're good to process the tweet
    increment_counter("tweets_processed_this_interval")
    increment_counter("tweets_processed")

    susp_score = 0
    created_at = status["created_at"]
    screen_name = user["screen_name"]
    tweet_id = status["id_str"]
    user_id = user["id_str"]
    lang = status["lang"]
    text = text.strip()
    text = re.sub("\n", " ", text)

# Create some useable time formats
    tweet_time_object = twitter_time_to_object(created_at)
    tweet_time_unix = twitter_time_to_unix(created_at)
    tweet_hour_label = tweet_time_object.strftime("%Y%m%d%H")
    tweet_day_label = tweet_time_object.strftime("%Y%m%d")
    tweet_url = "https://twitter.com/" + screen_name + "/status/" + tweet_id

    record_interarrival(screen_name, tweet_time_unix)
    interarrival_stdev = calculate_interarrival_statistics(screen_name)
    if interarrival_stdev > 0:
        if "interarrival_matches" not in data:
            data["interarrival_matches"] = {}
        data["interarrival_matches"][screen_name] = interarrival_stdev

# Dump raw status to json
    if "dump_raw_data" in conf["settings"]:
        if conf["settings"]["dump_raw_data"] == True:
            dump_file_handle.write((unicode(json.dumps(status,ensure_ascii=False))) + u"\n")

# Dump tweet to disk
    if "record_all_tweets" in conf["settings"]:
        if conf["settings"]["record_all_tweets"] == True:
            tweet_file_handle.write(unicode(text) + u"\n")
            tweet_url_file_handle.write(unicode(tweet_url) + u"\t" + unicode(text) + u"\n")

# Check text for keywords
    if "keywords" in conf:
        keywords = conf["keywords"]
        matched = False
        for k in keywords:
            if k.lower() in text.lower():
                matched = True
        if matched == True:
            record_list("keyword_matches", screen_name)

# Process text, record who tweeted what, and build tag map
    debug_print("Preprocess text")
    preprocessed = preprocess_text(text)
    if "tag_map" not in data:
        data["tag_map"] = {}
    if preprocessed is not None:
        record_map("who_tweeted_what", preprocessed, screen_name, False)
        tags = []
        if preprocessed not in data["tag_map"]:
            if lang in nlp and lang in stemmer:
                debug_print("Processing with spacy")
                tags = process_sentence_nlp(preprocessed, nlp[lang], stemmer[lang])
            elif stopwords is not None and lang in stopwords:
                debug_print("Tokenizing with stopwords")
                tags = tokenize_sentence(preprocessed, stopwords[lang])
            else:
                debug_print("Tokenizing without stopwords")
                tags = tokenize_sentence(preprocessed)
            if tags is not None and len(tags) > 0:
                debug_print("Adding tags to tag map")
                data["tag_map"][preprocessed] = tags
        else:
            tags = data["tag_map"][preprocessed]
        for t in tags:
            record_freq_dist("word_frequencies", t)


# Process hashtags
    ignore_list = conf["ignore"]
    debug_print("Process hashtags")
    monitored_hashtags = []
    if "monitored_hashtags" in conf:
        monitored_hashtags = conf["monitored_hashtags"]
    hashtags = get_hashtags(status)
    if len(hashtags) > 0:
        matched = False
        for h in hashtags:
            if h in monitored_hashtags:
                matched = True
            record_freq_dist("all_hashtags", h)
        if screen_name not in ignore_list:
            for h in hashtags:
                record_freq_dist_map("user_hashtag_map", screen_name, h, False)
        if matched == True:
            record_list("hashtag_matches", screen_name)

# Process URLs
    debug_print("Process URLs")
    urls = get_urls(status)
    fake_news_sources = conf["fake_news_sources"]
    url_keywords = conf["url_keywords"]
    tweeted_fake_news = False
    if len(urls) > 0:
        for u in urls:
            record_freq_dist("all_urls", u)
            if "twitter" not in u:
                record_freq_dist("urls_not_twitter", u)
            matched = False
            for k in url_keywords:
                if k in u:
                    matched = True
            if matched == True:
                record_list("url_matches", u)
            for f in fake_news_sources:
                if f in u:
                    tweeted_fake_news = True
                    record_freq_dist("fake_news_urls", u)
    if tweeted_fake_news == True:
        record_freq_dist("fake_news_tweeters", screen_name)

# Process interactions
    debug_print("Process interactions")
    interactions = get_interactions(status)
    bad_users = conf["bad_users"]
    good_users = conf["good_users"]
    if len(interactions) > 0:
        for n in interactions:
            record_freq_dist("all_mentioned", n)
        if screen_name not in ignore_list:
            for n in interactions:
                if n not in ignore_list:
                    record_freq_dist_map("user_user_map", screen_name, n, False)
                matched = False
                if screen_name not in good_users:
                    for u in bad_users:
                        if u == n:
                            matched = True
                if matched == True:
                    record_list("interacted_with_bad", screen_name)

# Processing description
    if "description_keywords" in conf:
        keywords = conf["description_keywords"]
        if "description" in user:
            description = user["description"]
            if description is not None:
                description = description.lower()
                matched = False
                for k in keywords:
                    if k.lower() in description:
                        matched = True
                if matched == True:
                    record_list("description_matches", screen_name)

    debug_print("Done processing")
    return


def preprocess_tweet(status):
    debug_print(sys._getframe().f_code.co_name)
    increment_counter("tweets_encountered")
    debug_print("Preprocessing status")
    if status is None:
        debug_print("No status")
        sys.stdout.write("-")
        sys.stdout.flush()
        return
    if "lang" not in status:
        debug_print("No lang")
        sys.stdout.write("-")
        sys.stdout.flush()
        return
    lang = status["lang"]
    debug_print("lang="+lang)
    increment_counter("tweets_" + lang)
    if len(conf["languages"]) > 0:
        if lang not in conf["languages"]:
            debug_print("Skipping tweet of lang: " + lang)
            sys.stdout.write("-")
            sys.stdout.flush()
            return
    increment_counter("captured_tweets_" + lang)
    increment_counter("tweets_captured")
    increment_counter("tweets_captured_this_interval")
    tweet_queue.put(status)
    sys.stdout.write("#")
    sys.stdout.flush()

def tweet_processing_thread():
    debug_print(sys._getframe().f_code.co_name)
    while True:
        item = tweet_queue.get()
        process_tweet(item)
        dump_event()
        tweet_queue.task_done()
    return

def start_thread():
    debug_print(sys._getframe().f_code.co_name)
    global tweet_queue
    print "Starting processing thread..."
    tweet_queue = Queue.Queue()
    t = threading.Thread(target=tweet_processing_thread)
    t.daemon = True
    t.start()
    return

def get_tweet_stream(query):
    debug_print(sys._getframe().f_code.co_name)
    if follow == True:
        for tweet in t.filter(follow=query):
            preprocess_tweet(tweet)
    elif search == True:
        for tweet in t.search(query):
            preprocess_tweet(tweet)
    else:
        if query == "":
            for tweet in t.sample():
                preprocess_tweet(tweet)
        else:
            for tweet in t.filter(track=query):
                preprocess_tweet(tweet)

#########################################
# Main routine, called when script starts
#########################################
if __name__ == '__main__':
    follow = False
    input_params = []
    if len(sys.argv) > 1:
        for s in sys.argv[1:]:
            if "search" in s:
                search = True
            elif "follow" in s:
                follow = True
            elif "debug" in s:
                debug = True
            else:
                input_params.append(s)

    if search == True and follow == True:
        print("Only one of search and follow params can be supplied")
        sys.exit(0)

    directories = ["data", "data/custom", "data/raw", "data/json", "data/json/overall", "data/json/daily", "data/json/hourly", "data/graphs", "data/graphs/overall", "data/graphs/daily", "data/graphs/hourly", "data/hourly", "data/daily", "data/overall"]
    for dir in directories:
        if not os.path.exists(dir):
            os.makedirs(dir)

    if os.path.exists("config/stopwords.json"):
        stopwords = load_json("config/stopwords.json")

# Deserialize from previous run
    data = {}
    filename = "data/raw/serialized.json"
    old_data = load_json(filename)
    if old_data is not None:
        data = old_data
    data = {}

    tweet_day_label = ""
    tweet_hour_label = ""
    day_label = get_datestring("day")
    if day_label not in data:
        data[day_label] = {}
    hour_label = get_datestring("hour")
    if hour_label not in data:
        data[hour_label] = {}

    load_settings()
    set_counter("dump_interval", conf["params"]["default_dump_interval"])
    set_counter("serialization_interval", conf["params"]["serialization_interval"])
    set_counter("graph_dump_interval", conf["params"]["graph_dump_interval"])
    set_counter("previous_dump_time", int(time.time()))
    set_counter("previous_graph_dump_time", int(time.time()))
    set_counter("script_start_time", int(time.time()))
    set_counter("previous_serialize", int(time.time()))
    set_counter("previous_config_reload", int(time.time()))

    tweet_file_handle = io.open("data/raw/tweets.txt", "a", encoding="utf-8")
    tweet_url_file_handle = io.open("data/raw/tweet_urls.txt", "a", encoding="utf-8")
    dump_file_handle = io.open("data/raw/raw.json", "a", encoding="utf-8")
    volume_file_handle = open("data/raw/tweet_volumes.txt", "a")

# Init spacy
    nlp = {}
    stemmer = {}
    print("Languages: " + ", ".join(conf["languages"]))
    for l in conf["languages"]:
        if l in spacy_supported_langs:
            nlp[l] = spacy.load(l)
            if nlp[l] is not None:
                print("Loaded NLP processor for language: " + l)
        if l in lang_map.keys():
            stemmer[l] = SnowballStemmer(lang_map[l])
            if stemmer[l] is not None:
                print("Loaded stemmer for language: " + l)

# Initialize twitter object
    acct_name, consumer_key, consumer_secret, access_token, access_token_secret = get_account_credentials()
    t = Twarc(consumer_key, consumer_secret, access_token, access_token_secret)
    print "Signing in as: " + acct_name

# Determine mode and build query
    query = ""
    if follow == True:
        print("Listening to accounts")
        to_follow = []
        if len(input_params) > 0:
            to_follow = input_params
        else:
            to_follow = read_config("config/follow.txt")
            to_follow = [x.lower() for x in to_follow]
        id_list_file = "config/id_list.json"
        id_list = []
        if os.path.exists(id_list_file):
            id_list = load_json(id_list_file)
        if id_list is None or len(id_list) < 1:
            print("Converting names to IDs")
            if len(to_follow) < 1:
                print("No account names provided.")
                sys.exit(0)
            id_list = get_ids_from_names(to_follow)
            save_json(id_list, id_list_file)
        print(" ID count: " + str(len(id_list)))
        if len(id_list) < 1:
            print("No account IDs found.")
            sys.exit(0)
        query = ",".join(id_list)
        print "Preparing stream"
        print "IDs: " + query
    elif search == True:
        print("Performing Twitter search")
        searches = []
        if len(input_params) > 0:
            searches = input_params
        else:
            searches = read_config("config/search.txt")
            searches = [x.lower() for x in searches]
        if len(searches) < 1:
            print("No search terms supplied.")
            sys.exit(0)
        if len(searches) > 1:
            print("Search can only handle one search term (for now).")
            sys.exit(0)
        query = searches[0]
        print "Preparing search"
        print "Query: " + query
    else:
        print("Listening to Twitter search stream")
        targets = []
        if len(input_params) > 0:
            targets = input_params
        else:
            targets = read_config("config/targets.txt")
        if len(targets) > 0:
            query = ",".join(targets)
            print "Preparing stream"
            if query == "":
                print "Getting 1% sample."
            else:
                print "Search: " + query

# Start a thread to process incoming tweets
    start_thread()

# Start stream
    script_start_time_str = time.strftime("%Y-%m-%d %H:%M:%S")
    filename = "data/raw/conf.json"
    save_json(conf, filename)
    if search == True:
        set_counter("successful_loops", 0)
        try:
            get_tweet_stream(query)
        except KeyboardInterrupt:
            print "Keyboard interrupt..."
            cleanup()
            sys.exit(0)
        except:
            print
            print "Something exploded..."
            cleanup()
            sys.exit(0)
        cleanup()
        sys.exit(0)
    else:
        while True:
            set_counter("successful_loops", 0)
            try:
                get_tweet_stream(query)
            except KeyboardInterrupt:
                print "Keyboard interrupt..."
                cleanup()
                sys.exit(0)
            except:
                print
                print "Something exploded..."
                cleanup()
                sys.exit(0)



# ToDo:
# Search mode, multiple params - different save dirs, or overlapping
# Retweet spikes - also in search mode
# Interface the clustering code
# Suspiciousness
# bot detection
# sources