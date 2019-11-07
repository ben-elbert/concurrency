# thumbnail_maker.py
import time
import os
import logging
from urllib.parse import urlparse
from urllib.request import urlretrieve
from queue import Queue
from threading import Thread

import PIL
from PIL import Image

FORMAT = "[%(threadName)s, %(asctime)s, %(levelname)s] %(message)s"
logging.basicConfig(filename='logfile.log', level=logging.DEBUG, format=FORMAT)

class ThumbnailMakerService(object):
    def __init__(self, home_dir='.'):
        self.home_dir = home_dir
        self.input_dir = self.home_dir + os.path.sep + 'incoming'
        self.output_dir = self.home_dir + os.path.sep + 'outgoing'
        self.img_queue = Queue()
        self.dl_queue = Queue()

    def download_image(self):
        # We are doing double check for the emptiness of the dl_queue
        # this is done, because there is a situation when a threads comes in there is
        # a last item in the queue, and it asks if the queue is empty, and gets a false result
        # then it gets suspended and another thread is getting that last item from the queue
        # then the first thread is resumed and, trying to get that item, but the queue is empty,
        # therefore an exception of queue.Empty is being thrown, and if we don't catch it we
        # will exit.
        # in the except block we don't need to do anything because the thread will ask the while loop
        # is the queue is empty and now it will get a true result, and will finish it's work
        while not self.dl_queue.empty():
            try:
                url = self.dl_queue.get(block=False)
                # download each image and save to the input dir
                img_filename = urlparse(url).path.split('/')[-1]
                urlretrieve(url, self.input_dir + os.path.sep + img_filename)
                self.img_queue.put(img_filename)

                self.dl_queue.task_done()
            except Queue.Empty:
                logging.info('Queue empty')

    def download_images(self, img_url_list):
        # validate inputs
        if not img_url_list:
            return
        os.makedirs(self.input_dir, exist_ok=True)

        logging.info("beginning image downloads")

        start = time.perf_counter()
        for url in img_url_list:
            # download each image and save to the input dir
            img_filename = urlparse(url).path.split('/')[-1]
            urlretrieve(url, self.input_dir + os.path.sep + img_filename)
            self.img_queue.put(img_filename)
        end = time.perf_counter()

        self.img_queue.put(None)
        logging.info("downloaded {} images in {} seconds".format(len(img_url_list), end - start))

    def perform_resizing(self):
        # validate inputs
        os.makedirs(self.output_dir, exist_ok=True)

        logging.info("beginning image resizing")
        target_sizes = [32, 64, 200]
        num_images = len(os.listdir(self.input_dir))

        start = time.perf_counter()
        while True:
            filename = self.img_queue.get()
            if filename:
                logging.info("resizing image {}".format(filename))
                orig_img = Image.open(self.input_dir + os.path.sep + filename)
                for basewidth in target_sizes:
                    img = orig_img
                    # calculate target height of the resized image to maintain the aspect ratio
                    wpercent = (basewidth / float(img.size[0]))
                    hsize = int((float(img.size[1]) * float(wpercent)))
                    # perform resizing
                    img = img.resize((basewidth, hsize), PIL.Image.LANCZOS)

                    # save the resized image to the output dir with a modified file name
                    new_filename = os.path.splitext(filename)[0] + \
                        '_' + str(basewidth) + os.path.splitext(filename)[1]
                    img.save(self.output_dir + os.path.sep + new_filename)

                os.remove(self.input_dir + os.path.sep + filename)
                logging.info("done resizing image {}".format(filename))
                self.img_queue.task_done()
            # This else is when the posion pill consumed and the message is None
            else:
                self.img_queue.task_done()
                break
        end = time.perf_counter()

        logging.info("created {} thumbnails in {} seconds".format(num_images, end - start))

    def make_thumbnails(self, img_url_list):
        logging.info("START make_thumbnails")

        start = time.perf_counter()
        # Here we are downloading all the images and storing them into a mq 
        # dl_queue, we did this in order to avoid, many threads downloading
        # from the resource concurrently, and my cause a ddos to the web service.
        for img_url in img_url_list:
            self.dl_queue.put(img_url)
        # Here we are triggering 4 threads that will get a url from the dl_queue
        # and then will download the image, after each thread is finish with the download
        # it will mark the task as task_done, we need this because dl_queue is triggered a
        # dl_queue.join() method in order the know when threads finished downloading all images
        # before we used a second queue for storing the url's, we had only 1 queue,
        # that the threads stored the images inside, and the thread that is responsible for 
        # the resizing consumed the images from that queue
        num_dl_threads = 4
        for _ in range(num_dl_threads):
            t = Thread(target=self.download_image)
            t.start()
        # triggering a thread for handle all the downloaded images, the was putted inside the
        # img_queue
        t2 = Thread(target=self.perform_resizing)
        t2.start()

        # In order for us to know when to put the poison pill described below, we will wait for
        # all the tasks in the dl_queue to be done, and then we know that all images were downloaded
        # and now we can put the poison pill inside the img_queue, for the resize thread to consume 
        # and exit the infinite loop
        self.dl_queue.join()
        # This is how the thread responsible for resizing will know how to finish his work
        # it is called a posion pill, the resizing thread will consume this None message and 
        # will to know that all images were resized

        self.img_queue.put(None)
        # we don't want to join here for the 4 producer threads becuase the important threads
        # is the resize thread, and that's because we know that when the resize thread is done,
        # there are no more images to resize and therefore we can exit
        t2.join()

        end = time.perf_counter()
        logging.info("END make_thumbnails in {} seconds".format(end - start))
