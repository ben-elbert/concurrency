# thumbnail_maker.py
import time
import os
import logging
from urllib.parse import urlparse
from urllib.request import urlretrieve
from queue import Queue
from threading import Thread, Lock
import multiprocessing
import PIL
from PIL import Image

FORMAT = "[%(threadName)s, %(asctime)s, %(levelname)s] %(message)s"
logging.basicConfig(filename='logfile.log', level=logging.DEBUG, format=FORMAT)

class ThumbnailMakerService(object):
    def __init__(self, home_dir='.'):
        self.home_dir = home_dir
        self.input_dir = self.home_dir + os.path.sep + 'incoming'
        self.output_dir = self.home_dir + os.path.sep + 'outgoing'
        # Joinable queue, is a replacement for the regluar queue
        # it provides the task_done and join methods which 
        # standard queue doesn't support
        self.img_queue = multiprocessing.JoinableQueue()
        self.dl_size = 0
        # we decided to use the Value shared memory, that the multiprocess offers
        # but we could also use the queue, each process would update the queue
        # and then the parent process would aggregate all the sizes and output it
        # multiprocessing gives us a way to share memory between processes
        # 1. manager process - if we needed to share a list of items then this would be the better choice
        # 2. shared memory - because we only need to share one value there is no need for the manager process
        # we can use the Value and Array which takes a ctype
        self.resized_size = multiprocessing.Value('i', 0)

    # now we have to pass as arguments the dl_queue and dl_size_lock,
    # this is because they are not picklable, so we could spwan a new process
    def download_image(self, dl_queue, dl_size_lock):
        while not dl_queue.empty():
            try:
                url = dl_queue.get(block=False)
                # download each image and save to the input dir
                img_filename = urlparse(url).path.split('/')[-1]
                img_filepath = self.input_dir + os.path.sep + img_filename
                urlretrieve(url, img_filepath)
                with dl_size_lock:
                    self.dl_size += os.path.getsize(img_filepath)

                self.img_queue.put(img_filename)

                dl_queue.task_done()
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
                    out_filepath = self.output_dir + os.path.sep + new_filename
                    img.save(out_filepath)

                    # because we are doing two atomic operations, the internal Value lock is only promise to make
                    # one operation atomic, so we need to lock by ourselfes in order to avoid races
                    # we are using the internal RLock that the shared memory has
                    with self.resized_size.get_lock():
                        self.resized_size.value += os.path.getsize(out_filepath)

                os.remove(self.input_dir + os.path.sep + filename)
                logging.info("done resizing image {}".format(filename))
                self.img_queue.task_done()
            else:
                self.img_queue.task_done()
                break

    def make_thumbnails(self, img_url_list):
        logging.info("START make_thumbnails")
        start = time.perf_counter()

        dl_queue = Queue()
        dl_size_lock = Lock()

        for img_url in img_url_list:
            dl_queue.put(img_url)

        num_dl_threads = 4
        for _ in range(num_dl_threads):
            t = Thread(target=self.download_image, args=(dl_queue, dl_size_lock))
            t.start()

        num_processes = multiprocessing.cpu_count()
        for _ in range(num_processes):
            p = multiprocessing.Process(target=self.perform_resizing)
            p.start()

        dl_queue.join()
        # in order to not terminate only the process that will consume a one none message
        # we will put poison pills as many processes that we have
        for _ in range(num_processes):
            self.img_queue.put(None)

        end = time.perf_counter()
        logging.info("END make_thumbnails in {} seconds".format(end - start))
        logging.info("Initial size of downloads: [{}]  Final size of images: [{}]".format(self.dl_size, self.resized_size.value))
