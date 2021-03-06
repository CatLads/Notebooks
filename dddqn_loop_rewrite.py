# -*- coding: utf-8 -*-
"""DDDQN.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/github/CatLads/Notebooks/blob/main/DDDQN.ipynb
"""

from flatland.envs.rail_generators import sparse_rail_generator
from flatland.envs.schedule_generators import sparse_schedule_generator
from flatland.envs.rail_env import RailEnv, RailEnvActions
from flatland.envs.rail_generators import complex_rail_generator
from flatland.envs.observations import GlobalObsForRailEnv, TreeObsForRailEnv
from flatland.utils.rendertools import RenderTool
from obs_utils import normalize_observation, format_action_prob
from gym import spaces
from tqdm import tqdm
from os import path
import sys
import os
import glob
import wandb
wandb.init(project='flatlands', entity='fatlads', tags=["base_baseline", "dddqn"])
config = wandb.config
seed = 69  # nice

width = 15  # @param{type: "integer"}
height = 15  # @param{type: "integer"}
num_agents = 3  # @param{type: "integer"}
tree_depth = 2  # @param{type: "integer"}
radius_observation = 10
WINDOW_LENGTH = 22  # @param{type: "integer"}

random_rail_generator = complex_rail_generator(
    nr_start_goal=10,  # @param{type:"integer"} number of start and end goals
    # connections, the higher the easier it should be for
    # the trains
    nr_extra=10,  # @param{type:"integer"} extra connections
    # (useful for alternite paths), the higher the easier
    min_dist=10,
    max_dist=99999,
    seed=seed
)

env = RailEnv(
    width=width,
    height=height,
    rail_generator=random_rail_generator,
    obs_builder_object=TreeObsForRailEnv(tree_depth),
    number_of_agents=num_agents
)

obs, info = env.reset()

env_renderer = RenderTool(env)

state_shape = normalize_observation(obs[0], tree_depth, radius_observation).shape
action_shape = (5,)

import tensorflow as tf
import numpy as np
from tensorflow.keras.models import load_model


class DDDQN(tf.keras.Model):
    def __init__(self):
        super(DDDQN, self).__init__()
        self.d1 = tf.keras.layers.Dense(128, activation='relu')
        self.d2 = tf.keras.layers.Dense(128, activation='relu')
        self.v = tf.keras.layers.Dense(1, activation=None)
        self.a = tf.keras.layers.Dense(5, activation=None)  # TODO: Change this please

    def call(self, input_data):
        x = self.d1(input_data)
        x = self.d2(x)
        v = self.v(x)
        a = self.a(x)
        Q = v + (a - tf.math.reduce_mean(a, axis=1, keepdims=True))
        return Q

    def advantage(self, state):
        x = self.d1(state)
        x = self.d2(x)
        a = self.a(x)
        return a


class exp_replay():
    def __init__(self, buffer_size=1000000):
        self.buffer_size = buffer_size

        self.state_mem = np.zeros((self.buffer_size, *state_shape), dtype=np.float32)
        self.action_mem = np.zeros((self.buffer_size),
                                   dtype=np.int32)  # SIMMY: I removed , *action_shape cause we should just save one action per agent, not all of them
        self.reward_mem = np.zeros((self.buffer_size), dtype=np.float32)
        self.next_state_mem = np.zeros((self.buffer_size, *state_shape), dtype=np.float32)
        self.done_mem = np.zeros((self.buffer_size), dtype=np.bool)
        self.pointer = 0

    def add_exp(self, state, action, reward, next_state, done):
        idx = self.pointer % self.buffer_size
        self.state_mem[idx] = state
        self.action_mem[idx] = action
        self.reward_mem[idx] = reward
        self.next_state_mem[idx] = next_state
        self.done_mem[idx] = 1 - int(done)
        self.pointer += 1

    def sample_exp(self, batch_size=64):
        max_mem = min(self.pointer, self.buffer_size)
        batch = np.random.choice(max_mem, batch_size, replace=False)
        states = self.state_mem[batch]
        actions = self.action_mem[batch]
        rewards = self.reward_mem[batch]
        next_states = self.next_state_mem[batch]
        dones = self.done_mem[batch]
        return states, actions, rewards, next_states, dones


class agent():
    def __init__(self, gamma=0.99, replace=100, lr=0.001):
        self.gamma = gamma
        self.epsilon = 1.0
        self.min_epsilon = 0.01
        self.epsilon_decay = 1e-3
        self.replace = replace
        self.trainstep = 0
        self.memory = exp_replay()
        self.batch_size = 64
        self.q_net = DDDQN()
        self.target_net = DDDQN()
        opt = tf.keras.optimizers.Adam(learning_rate=lr)
        self.q_net.compile(loss='mse', optimizer=opt)
        self.target_net.compile(loss='mse', optimizer=opt)

    def act(self, state):
        if np.random.rand() <= self.epsilon:
            return np.random.choice([i for i in range(5)])  # TODO: change the 5

        else:
            actions = self.q_net.advantage(np.array([state]))
            action = np.argmax(actions)
            return action

    def update_mem(self, state, action, reward, next_state, done):
        self.memory.add_exp(state, action, reward, next_state, done)

    def update_target(self):
        self.target_net.set_weights(self.q_net.get_weights())

    def update_epsilon(self):
        self.epsilon = self.epsilon - self.epsilon_decay if self.epsilon > self.min_epsilon else self.min_epsilon
        return self.epsilon

    def train(self):
        if self.memory.pointer < self.batch_size:
            return

        if self.trainstep % self.replace == 0:
            self.update_target()
        states, actions, rewards, next_states, dones = self.memory.sample_exp(self.batch_size)
        target = self.q_net.predict(states)
        next_state_val = self.target_net.predict(next_states)
        max_action = np.argmax(self.q_net.predict(next_states), axis=1)
        batch_index = np.arange(self.batch_size, dtype=np.int32)
        q_target = np.copy(target)
        q_target[batch_index, actions] = rewards + self.gamma * next_state_val[batch_index, max_action] * dones
        self.q_net.train_on_batch(states, q_target)
        self.update_epsilon()
        self.trainstep += 1

    def save_model(self):
        self.q_net.save_weights("alternative_model")
        self.target_net.save_weights("alternative_target_model")
        print("model saved")

    def load_model(self):
        self.q_net.load_weights("alternative_model")
        self.target_net.load_weights("alternative_target_model")
        print("model loaded")


agent007 = agent()
if (glob.glob("alternative_model") != []):
    agent007.load_model()
# Train for 300 episodes
saving_interval = 50
max_steps = env._max_episode_steps
smoothed_normalized_score = -1.0
smoothed_completion = 0.0
action_count = [0] * action_shape[0]
action_dict = dict()
agent_obs = [None] * num_agents
agent_prev_obs = [None] * num_agents
agent_prev_action = [2] * num_agents
update_values = [False] * num_agents

for episode in range(3000):
    try:
        # Initialize episode
        obs, info = env.reset(regenerate_rail=True, regenerate_schedule=True)
        # env_renderer = RenderTool(env)
        done = {i: False for i in range(0, num_agents)}
        done["__all__"] = False
        scores = 0
        step_counter = 0;

        for agent in env.get_agent_handles():
            if obs[agent]:
                agent_obs[agent] = normalize_observation(obs[agent], tree_depth, observation_radius=radius_observation)
                agent_prev_obs[agent] = agent_obs[agent].copy()

        for step in range(max_steps - 1):
            actions = {}
            agents_obs = {}

            for agent in env.get_agent_handles():
                if info['action_required'][agent]:
                    update_values[agent] = True
                    action = agent007.act(agent_obs[agent])

                    action_count[action] += 1
                    #actions_taken.append(action)
                else:
                    # An action is not required if the train hasn't joined the railway network,
                    # if it already reached its target, or if is currently malfunctioning.
                    update_values[agent] = False
                    action = 0
                action_dict.update({agent: action})

            next_obs, all_rewards, done, info = env.step(action_dict)  # base env
            # env_renderer.render_env(show=True)

            # Update replay buffer and train agent
            for agent in env.get_agent_handles():
                if update_values[agent] or done['__all__']:
                    # Only learn from timesteps where somethings happened
                    agent007.update_mem(agent_prev_obs[agent], agent_prev_action[agent], all_rewards[agent], agent_obs[agent], done[agent])
                    agent007.train()
                    agent_prev_obs[agent] = agent_obs[agent].copy()
                    agent_prev_action[agent] = action_dict[agent]

                # Preprocess the new observations
                if next_obs[agent]:

                    agent_obs[agent] = normalize_observation(next_obs[agent], tree_depth,
                                                             observation_radius=radius_observation)

                scores += all_rewards[agent]

            if done['__all__']:
                break



        tasks_finished = sum(done[idx] for idx in env.get_agent_handles())
        if (step_counter < max_steps - 1):
            completion = tasks_finished / max(1, env.get_num_agents())
        normalized_score = scores / (max_steps * env.get_num_agents())
        smoothing = 0.99
        smoothed_normalized_score = smoothed_normalized_score * smoothing + normalized_score * (1.0 - smoothing)
        smoothed_completion = smoothed_completion * smoothing + completion * (1.0 - smoothing)
        action_probs = action_count / np.sum(action_count)
        action_count = [1] * action_shape[0]
        step_counter += 1
        wandb.log({
            "normalized_score": normalized_score,
            "smoothed_normalized_score": smoothed_normalized_score,
            "completion": 100*completion,
            "smoothed_completion": 100*smoothed_completion
        })
        print(
            '\r???? Episode {}'
            '\t ???? Score: {:.3f}'
            ' Avg: {:.3f}'
            '\t ???? Done: {:.2f}%'
            ' Avg: {:.2f}%'
            '\t ???? Action Probs: {}'
            '\n'.format(
                episode,
                normalized_score,
                smoothed_normalized_score,
                100 * completion,
                100 * smoothed_completion,
                format_action_prob(action_probs)
            ), end=" ")

        if (episode % saving_interval == 0):
            agent007.save_model()
        # sum_rewards += reward
    except KeyboardInterrupt:
        print('Interrupted')
        agent007.save_model()
        try:
            sys.exit(0)
        except SystemExit:
            os._exit(0)

