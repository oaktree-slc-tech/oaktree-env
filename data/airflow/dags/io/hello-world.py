# ETL Example 1: AirFlow "Hello World".
# This example shows a set of tasks that echo "Hello World" using BASH.
# It demonstrates the use of the BASH operator.

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator


# Default arguments
default_args = {
	'owner': 'sonador',
	'depends_on_past': False,
	'start_date': datetime(2024, 1, 1),
	'retries': 1,
	'retry_delay': timedelta(minutes=1),
}


# Initialize HelloWorld DAG
dag = DAG('HelloWorld', default_args=default_args)


# Define task stages
t1 = BashOperator(task_id='task-1', bash_command='echo "Hello World from Task 1"', dag=dag)
t2 = BashOperator(task_id='task-2', bash_command='echo "Hello World from Task 2"', dag=dag)
t3 = BashOperator(task_id='task-3', bash_command='echo "Hello World from Task 3"', dag=dag)
t4 = BashOperator(task_id='task-4', bash_command='echo "Hello World from Task 4"', dag=dag)


# Order the tasks sequentially
tasks = [t1, t2, t3, t4]
for i, t in enumerate(tasks):

	if i == 0:
		t.set_downstream(tasks[i+1])

	if i > 0 :
		t.set_upstream(tasks[i-1])
