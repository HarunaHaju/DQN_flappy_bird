import random

import numpy as np
import tensorflow as tf

import replay_buffer

# replay buffer, target network, dueling
SIGN = 'dueling_DQN'

class DeepQNetworks:
    def __init__(self, n_actions, 
        starter_learning_rate=0.000025, 
        gamma=0.99, 
        memory_size=50000, 
        batch_size=32,
        n_explore=10000,
        frame_per_action=4,
        replace_target_iter=500):
        self.n_actions = n_actions
        self.gamma = gamma
        self.memory_size = memory_size
        self.batch_size = batch_size
        self.n_explore = n_explore
        self.frame_per_action = frame_per_action
        self.replace_target_iter = replace_target_iter
        
        self.time_step = 0
        self.replay_memory = replay_buffer.ReplayBuffer(memory_size)

        self.global_step = tf.Variable(0, trainable=False, name='global_step')
        self.lr = tf.train.exponential_decay(starter_learning_rate, self.global_step, 10000, 0.96)
        self.createNetwork()
        q_params = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope='Q_network')
        t_params = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope='target_network')
        self.replace_target_op = [tf.assign(t, q) for t, q in zip(t_params, q_params)]

        self.merged = tf.summary.merge_all()
        self.saver = tf.train.Saver()
        self.sess = tf.Session()      
        self.sess.run(tf.global_variables_initializer())
        self.sess.graph.finalize()

        ckpt = tf.train.get_checkpoint_state(SIGN)
        if ckpt and ckpt.model_checkpoint_path:
            self.saver.restore(self.sess, ckpt.model_checkpoint_path)
            print("Successfully loaded:", ckpt.model_checkpoint_path)
        else:
            print("Could not find old network weights")

        self.writer = tf.summary.FileWriter("logs/" + SIGN, self.sess.graph)

    def createNetwork(self):
        self.state_input = tf.placeholder(tf.float32, [None,80,80,4], name='state_input')
        self.target_state_input = tf.placeholder(tf.float32, [None,80,80,4], name='target_state_input')
        self.y_input = tf.placeholder(tf.float32, [None], name='y_input')
        self.action_input = tf.placeholder(tf.float32, [None, self.n_actions], name='action_input')

        def conv_layer(input, filter_size, channels_in, channels_out, strides, name='conv'):
            with tf.variable_scope(name):
                w = tf.get_variable('W', [filter_size, filter_size, channels_in, channels_out], initializer=tf.variance_scaling_initializer())
                b = tf.get_variable('B', [channels_out], initializer=tf.constant_initializer())
                conv = tf.nn.conv2d(input, w, strides=[1,strides,strides,1], padding='SAME')
                return tf.nn.relu(conv + b)

        def fc_layer(input, channels_in, channels_out, activation=None, name='fc'):
            with tf.variable_scope(name):
                w = tf.get_variable('W', [channels_in, channels_out], initializer=tf.variance_scaling_initializer())
                b = b_fc0 = tf.get_variable('B', [channels_out], initializer=tf.constant_initializer())
                fc = tf.matmul(input, w) + b
                if activation == None:
                    return fc
                else:
                    return activation(fc)

        with tf.variable_scope("Q_network"):
            conv1 = conv_layer(self.state_input, 8, 4, 32, 4, name='conv1')
            pool1 = tf.nn.max_pool(conv1, ksize=[1,2,2,1], strides=[1,2,2,1], padding='SAME', name='pool1')
            conv2 = conv_layer(pool1, 4, 32, 64, 2, name='conv2')
            conv3 = conv_layer(conv2, 3, 64, 64, 1, name='conv3')
            flattened = tf.reshape(conv3, [-1, 5 * 5 * 64])
            fc1 = fc_layer(flattened, 5 * 5 * 64, 512, activation=tf.nn.relu, name='fc1')
            with tf.variable_scope('Value'):
                V = fc_layer(fc1, 512, 1, name='V')
            with tf.variable_scope('Advantage'):
                A = fc_layer(fc1, 512, self.n_actions, name='A')
            self.Q_value = V + A
            tf.summary.scalar('mean_V_value', tf.reduce_mean(V))
            tf.summary.scalar('mean_A_value', tf.reduce_mean(A))
            tf.summary.scalar('mean_Q_value', tf.reduce_mean(self.Q_value))

        with tf.variable_scope('loss'):
            Q_action = tf.reduce_sum(tf.multiply(self.Q_value, self.action_input), reduction_indices = 1)
            self.loss = tf.reduce_mean(tf.square(self.y_input - Q_action))
            tf.summary.scalar('mean_loss', self.loss)
        with tf.variable_scope('train'):
            self.train_op = tf.train.AdamOptimizer(self.lr).minimize(self.loss, global_step=self.global_step)

        with tf.variable_scope("target_network"):
            conv1 = conv_layer(self.target_state_input, 8, 4, 32, 4, name='conv1')
            pool1 = tf.nn.max_pool(conv1, ksize=[1,2,2,1], strides=[1,2,2,1], padding='SAME', name='pool1')
            conv2 = conv_layer(pool1, 4, 32, 64, 2, name='conv2')
            conv3 = conv_layer(conv2, 3, 64, 64, 1, name='conv3')
            flattened = tf.reshape(conv3, [-1, 5 * 5 * 64])
            fc1 = fc_layer(flattened, 5 * 5 * 64, 512, activation=tf.nn.relu, name='fc1')
            with tf.variable_scope('Value'):
                V = fc_layer(fc1, 512, 1, name='V')
            with tf.variable_scope('Advantage'):
                A = fc_layer(fc1, 512, self.n_actions, name='A')
            self.Q_target = V + A

    def setInitState(self, observation):
        self.current_state = np.stack((observation, observation, observation, observation), axis = 2)

    def setPerception(self, next_observation, action, reward, terminal):
        new_state = np.append(self.current_state[:,:,1:], next_observation, axis = 2)
        self.replay_memory.add(self.current_state, action, reward, new_state, terminal)

        if self.time_step > self.batch_size:
            # Train the network
            self.trainQNetwork()

        self.current_state = new_state
        self.time_step += 1

    def trainQNetwork(self):
        # Step 1: obtain random minibatch from replay memory
        state_batch, action_batch, reward_batch, next_state_batch, terminal_batch = self.replay_memory.sample(self.batch_size)
        # Step 2: calculate y 
        Q_target_batch = self.sess.run(self.Q_target, feed_dict={self.target_state_input: next_state_batch})
        y_batch = np.where(terminal_batch, reward_batch, reward_batch + self.gamma * np.max(Q_target_batch, axis=1))
        
        summary, _ = self.sess.run([self.merged, self.train_op], feed_dict={
            self.state_input: state_batch,
            self.y_input: y_batch,
            self.action_input: action_batch})

        self.writer.add_summary(summary, self.sess.run(self.global_step))

        if self.time_step % self.replace_target_iter == 0:
            self.sess.run(self.replace_target_op)

        if self.sess.run(self.global_step) % 10000 == 0:
            self.saver.save(self.sess, SIGN + '/Qnetwork', global_step=self.global_step)

    def getAction(self):	
        action = np.zeros(self.n_actions)
        if self.sess.run(self.global_step) < self.n_explore:
            if self.sess.run(self.global_step) % self.frame_per_action == 0:
                Q_value = self.sess.run(self.Q_value, feed_dict={self.state_input: [self.current_state]})[0]
                action_index = random.randrange(self.n_actions) if random.random() <= 0.5 else np.argmax(Q_value)
                action[action_index] = 1
            else:
                action[0] = 1 # do nothing
        else:
            Q_value = self.sess.run(self.Q_value, feed_dict={self.state_input: [self.current_state]})[0]
            action[np.argmax(Q_value)] = 1

        return action

    def log_score(self, score):
        summary = tf.Summary(value=[tf.Summary.Value(tag='score', simple_value=score)])
        self.writer.add_summary(summary, self.sess.run(self.global_step))