import pyspark
from pyspark.sql import SparkSession
from pyspark.sql.functions import monotonically_increasing_id
from graphframes import *
from pyspark.sql.functions import desc, udf, col, lower, regexp_replace, explode, array_remove
from pyspark.ml.feature import Tokenizer, StopWordsRemover

spark = SparkSession.builder.appName('sg.edu.smu.is459.assignment2').getOrCreate()

# Load data
# posts_df = spark.read.load('/user/zzj/parquet-input/hardwarezone.parquet')
posts_df = spark.read.load('/Users/Deborah/Documents/GitHub_SMU/SMU-IS459/hadoop/hardwarezone.parquet')


# Clean the dataframe by removing rows with any null value
posts_df = posts_df.na.drop()

#posts_df.createOrReplaceTempView("posts")

# Find distinct users
#distinct_author = spark.sql("SELECT DISTINCT author FROM posts")
author_df = posts_df.select('author').distinct()

print('Author number :' + str(author_df.count()))

# Assign ID to the users
author_id = author_df.withColumn('id', monotonically_increasing_id())
author_id.show()

# Construct connection between post and author
left_df = posts_df.select('topic', 'author') \
    .withColumnRenamed("topic","ltopic") \
    .withColumnRenamed("author","src_author")

right_df =  left_df.withColumnRenamed('ltopic', 'rtopic') \
    .withColumnRenamed('src_author', 'dst_author')

#  Self join on topic to build connection between authors
author_to_author = left_df. \
    join(right_df, left_df.ltopic == right_df.rtopic) \
    .select(left_df.src_author, right_df.dst_author) \
    .distinct()
edge_num = author_to_author.count()
print('Number of edges with duplicate : ' + str(edge_num))

# Convert it into ids
id_to_author = author_to_author \
    .join(author_id, author_to_author.src_author == author_id.author) \
    .select(author_to_author.dst_author, author_id.id) \
    .withColumnRenamed('id','src')

id_to_id = id_to_author \
    .join(author_id, id_to_author.dst_author == author_id.author) \
    .select(id_to_author.src, author_id.id) \
    .withColumnRenamed('id', 'dst')

id_to_id = id_to_id.filter(id_to_id.src >= id_to_id.dst).distinct()

id_to_id.cache()

print("Number of edges without duplciate :" + str(id_to_id.count()))

# Build graph with RDDs
graph = GraphFrame(author_id, id_to_id)

# For complex graph queries, e.g., connected components, you need to set
# the checkopoint directory on HDFS, so Spark can handle failures.
# Remember to change to a valid directory in your HDFS
spark.sparkContext.setCheckpointDir('hadoop/bin/spark-checkpoint')

# get dataframe of connected components
sc.setCheckpointDir("/tmp/graphframes-example-connected-components")
communities = graph.connectedComponents()

# groupby and sort communities in desc order to show biggest communities
communities_count = communities.groupBy("component").count().sort(desc("count"))

# get list of top components (at least 2) in desc order 
top_components = communities_count.rdd.filter(lambda x: x[1]>1).map(lambda x: x[0]).collect()

# construct connection between author id and content posted
posts_id_df = posts_df.join(author_id, posts_df.author == author_id.author).select(posts_df.content, author_id.id)

# check for no null values
# >>> posts_id_df.filter(posts_id_df.content.isNull()).count()
# >>> posts_id_df.filter(posts_id_df.id.isNull()).count()

# clean content
df_clean = posts_id_df.select('id', (lower(regexp_replace('content', "[^a-zA-Z\\s]", "")).alias('content')))

# tokenize content
tokenizer = Tokenizer(inputCol='content', outputCol='words_token')
df_words_token = tokenizer.transform(df_clean).select('id', 'words_token')
df_cleaned_words_token = df_words_token.withColumn("cleaned_words_token", array_remove("words_token", '')).drop('words_token')

# Remove stop words
remover = StopWordsRemover(inputCol='cleaned_words_token', outputCol='words_clean')
df_words_no_stopw = remover.transform(df_cleaned_words_token).select('id', 'words_clean')

# get df with id, component, and cleaned words
comp_df = df_words_no_stopw.join(communities, communities.id == df_words_no_stopw.id).select(df_words_no_stopw.id, df_words_no_stopw.words_clean, communities.component)

# filter comp_df to show only comp 0
comp_0_df =  comp_df.filter(comp_df.component == 0)

# explode content to make a row for each word
df_comp_0_exploded = comp_0_df.select(comp_0_df.id, explode(comp_0_df.words_clean))

# get count of each unique word
df_comp_0_word_count = df_comp_0_exploded.groupBy("col").count().sort(desc("count"))


# filter comp_df to show only comp 0
comp_1_df =  comp_df.filter((comp_df.component != 0) & (comp_df.component.isin(top_components)))

# explode content to make a row for each word
df_comp_1_exploded = comp_1_df.select(comp_1_df.id, explode(comp_1_df.words_clean))

# get count of each unique word
df_comp_1_word_count = df_comp_1_exploded.groupBy("col").count().sort(desc("count"))


# create graph of comp 0
author_id_comp_0 = communities.filter(communities.component == 0)
graph_comp_0 = GraphFrame(author_id_comp_0, id_to_id)

# get triangle count
triangle_count = graph_comp_0.triangleCount()