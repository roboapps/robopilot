import time
from functools import partial

from kivy.clock import Clock
from kivy.core.window import Window
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.image import Image
from kivy.core.image import Image as CoreImage
from kivy.properties import NumericProperty, ObjectProperty, StringProperty, \
    ListProperty
from kivy.uix.popup import Popup
from kivy.lang.builder import Builder

import io
import os
from PIL import Image as PilImage
import pandas as pd
import plotly.express as px

from donkeycar import load_config
from donkeycar.management.tub_gui import RcFileHandler, decompose
from donkeycar.parts.tub_v2 import Tub
from donkeycar.pipeline.types import TubRecord


Builder.load_file('ui.kv')
Window.clearcolor = (0.2, 0.2, 0.2, 1)
HEIGHT = 60
rc_handler = RcFileHandler()


class FileChooserPopup(Popup):
    load = ObjectProperty()
    root_path = StringProperty()


class FileChooserBase:
    file_path = StringProperty("No file chosen")
    popup = ObjectProperty(None)
    root_path = os.path.expanduser('~')
    title = StringProperty(None)

    def open_popup(self):
        self.popup = FileChooserPopup(load=self.load,root_path=self.root_path,
                                      title=self.title)
        self.popup.open()

    def load(self, selection):
        self.file_path = str(selection[0])
        self.popup.dismiss()
        print(self.file_path)
        self.load_action()

    def load_action(self):
        pass


class ConfigManager(BoxLayout, FileChooserBase):
    """ Class to mange loading of the config file from the car directory"""
    config = ObjectProperty(None)
    file_path = StringProperty(rc_handler.data.get('car_dir', ''))

    def load_action(self):
        if self.file_path:
            try:
                self.config = load_config(os.path.join(self.file_path, 'config.py'))
                rc_handler.data['car_dir'] = self.file_path
            except FileNotFoundError:
                print(f'Directory {self.file_path} has no config.py')
            except Exception as e:
                print(e)


class TubLoader(BoxLayout, FileChooserBase):
    """ Class to manage loading or reloading of the Tub from the tub directory.
        Loading triggers many actions on other widgets of the app. """
    file_path = StringProperty(rc_handler.data.get('last_tub'))
    tub = ObjectProperty(None)
    len = NumericProperty(1)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.records = None

    def load_action(self):
        if self.update_tub(reload=False):
            rc_handler.data['last_tub'] = self.file_path

    def update_tub(self, reload=False):
        if not self.file_path:
            return False
        if not os.path.exists(os.path.join(self.file_path, 'manifest.json')):
            self.parent.parent.status(f'Path {self.file_path} is not a valid '
                                      f'tub.')
            return False
        try:
            self.tub = Tub(self.file_path)
        except Exception as e:
            self.parent.parent.status(f'Failed loading tub: {str(e)}')
            return False

        cfg = self.parent.parent.ids.config_manager.config
        expression = self.parent.parent.ids.tub_filter.filter_expression

        # Use filter, this defines the function
        def select(underlying):
            if not expression:
                return True
            else:
                try:
                    record = TubRecord(cfg, self.tub.base_path, underlying)
                    res = eval(expression)
                    return res
                except KeyError as err:
                    print(err)
                    return True

        self.records = [TubRecord(cfg, self.tub.base_path, record)
                        for record in self.tub if select(record)]
        self.len = len(self.records)
        if self.len > 0:
            self.parent.parent.index = 0
            self.parent.parent.ids.data_plot.update_dataframe_from_tub()
            msg = f'Loaded tub {self.file_path} with {self.len} records'
        else:
            msg = f'No records in tub {self.file_path}'
        if expression:
            msg += f' using filter {self.parent.parent.ids.tub_filter.record_filter}'
        self.parent.parent.status(msg)
        return True


class LabelBar(BoxLayout):
    field = StringProperty()
    field_property = ObjectProperty()
    config = ObjectProperty()
    msg = ''

    def update(self, record):
        field, index = decompose(self.field)
        self.msg = f'Adding {field}'
        if field in record.underlying:
            val = record.underlying[field]
            if index is not None:
                val = val[index]
            # update bar if present
            if self.field_property:
                max_val_key = self.field_property.max_value_id
                max = getattr(self.config, max_val_key, 1.0)
                if max == 1 and max_val_key:
                    self.msg += f' - could not find {max_val_key} in ' \
                                f'my_config.py, defaulting to 1.0'
                norm_val = val / max
                new_bar_val = (norm_val + 1) * 50 if \
                    self.field_property.centered else norm_val * 100
                self.ids.bar.value = new_bar_val
            self.ids.field_label.text = self.field
            if isinstance(val, float):
                text = f'{val:+07.3f}'
            elif isinstance(val, int):
                text = f'{val:10}'
            else:
                text = val
            self.ids.value_label.text = text
        else:
            print(f'Bad record {record.underlying["_index"]} - missing field '
                  f'{self.field}')


class DataPanel(BoxLayout):
    labels = {}

    def add_remove(self):
        field = self.ids.data_spinner.text
        if field is 'Add/remove':
            return
        if field in self.labels:
            self.remove_widget(self.labels[field])
            del(self.labels[field])
        else:
            field_property = rc_handler.field_properties.get(decompose(field)[0])
            cfg = self.parent.parent.ids.config_manager.config
            lb = LabelBar(field=field, field_property=field_property,
                          config=cfg)
            self.labels[field] = lb
            self.add_widget(lb)
            lb.update(self.parent.parent.current_record)
            self.parent.parent.status(lb.msg)
        self.parent.parent.ids.data_plot.plot_from_current_bars()
        self.ids.data_spinner.text = 'Add/remove'

    def update(self, record):
        for v in self.labels.values():
            v.update(record)


class FullImage(Image):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def update(self, record):
        try:
            img_arr = record.image()
            pil_image = PilImage.fromarray(img_arr)
            bytes_io = io.BytesIO()
            pil_image.save(bytes_io, format='png')
            bytes_io.seek(0)
            core_img = CoreImage(bytes_io, ext='png')
            self.texture = core_img.texture
        except KeyError as e:
            print('Missing key:', e)
        except Exception as e:
            print('Bad record:', e)


class ControlPanel(BoxLayout):
    speed = NumericProperty(1.0)
    record_display = StringProperty()
    clock = None
    fwd = None

    def start(self, fwd=True, continuous=False):
        time.sleep(0.1)
        call = partial(self.step, fwd)
        if continuous:
            self.fwd = fwd
            cycle_time = 1.0 / \
                (float(self.speed) *
                 self.parent.parent.ids.config_manager.config.DRIVE_LOOP_HZ)
        else:
            cycle_time = 0.08
        self.clock = Clock.schedule_interval(call, cycle_time)

    def step(self, fwd=True, *largs):
        new_index = self.parent.parent.index + (1 if fwd else -1)
        if new_index >= self.parent.parent.ids.tub_loader.len:
            new_index = 0
        elif new_index < 0:
            new_index = self.parent.parent.ids.tub_loader.len - 1
        self.parent.parent.index = new_index

    def stop(self):
        self.clock.cancel()

    def restart(self):
        if self.clock:
            self.stop()
            self.start(self.fwd, True)


class TubEditor(BoxLayout):
    lr = ListProperty([0, 0])

    def set_lr(self, is_l=True):
        """ Sets left or right range to the current tubrecord index """
        if not self.parent.current_record:
            return
        self.lr[0 if is_l else 1] \
            = self.parent.current_record.underlying['_index']

    def del_lr(self, is_del):
        """ Deletes or restores records in chosen range """
        tub = self.parent.ids.tub_loader.tub
        if self.lr[1] >= self.lr[0]:
            selected = list(range(*self.lr))
        else:
            last_id = tub.manifest.current_index
            selected = list(range(self.lr[0], last_id))
            selected += list(range(self.lr[1]))
        for d in selected:
            tub.delete_record(d) if is_del else tub.restore_record(d)


class TubFilter(BoxLayout):
    filter_expression = StringProperty(None)
    record_filter = StringProperty(rc_handler.data.get('record_filter', ''))

    def update_filter(self):
        filter_text = self.ids.record_filter.text
        # empty string resets the filter
        if filter_text == '':
            self.record_filter = ''
            self.filter_expression = ''
            rc_handler.data['record_filter'] = self.record_filter
            self.parent.status(f'Filter cleared')
            return
        filter_expression = self.create_filter_string(filter_text)
        try:
            record = self.parent.current_record
            res = eval(filter_expression)
            status = f'Filter result on current record: {res}'
            if isinstance(res, bool):
                self.record_filter = filter_text
                self.filter_expression = filter_expression
                rc_handler.data['record_filter'] = self.record_filter
            else:
                status += ' - non bool expression can\'t be applied'
            status += ' - press <Reload tub> to see effect'
            self.parent.status(status)
        except Exception as e:
            self.parent.status(f'Filter error on current record: {e}')

    def create_filter_string(self, filter_text, record_name='record'):
        """ Converts text like 'user/angle' into 'record.underlying['user/angle']
        so that it can be used in a filter. Will replace only expressions that
        are found in the tub inputs list.

        :param filter_text: input text like 'user/throttle > 0.1'
        :param record_name: name of the record in the expression
        :return:            updated string that has all input fields wrapped
        """
        for field in self.parent.current_record.underlying.keys():
            field_list = filter_text.split(field)
            if len(field_list) > 1:
                filter_text = f'{record_name}.underlying["{field}"]'\
                    .join(field_list)
        return filter_text


class DataPlot(BoxLayout):
    """ Data plot panel which embeds matplotlib interactive graph"""
    df = ObjectProperty()

    def plot_from_current_bars(self, in_app=True):
        """ Plotting from current selected bars. The DataFrame for plotting
            should contain all bars except for strings fields and all data is
            selected if bars are empty.  """
        field_map = dict(zip(self.parent.ids.tub_loader.tub.manifest.inputs,
                             self.parent.ids.tub_loader.tub.manifest.types))
        # Use selected fields or all fields if nothing is slected
        all_cols = self.parent.ids.data_panel.labels.keys() or self.df.columns
        cols = [c for c in all_cols if decompose(c)[0] in field_map
                and field_map[decompose(c)[0]] not in ('image_array', 'str')]

        df = self.df[cols]
        if df is None:
            return
        # Don't plot the milliseconds time stamp as this is a too big number
        df = df.drop(labels=['_timestamp_ms'], axis=1, errors='ignore')
        df = df.drop(labels=['cam/image_array'], axis=1, errors='ignore')
        df = df.drop(labels=['timestamp'], axis=1, errors='ignore')

        if in_app:
            self.parent.ids.graph.df = df
        else:
            fig = px.line(df, x=df.index, y=df.columns,
                          title=self.parent.ids.tub_loader.tub.base_path)
            fig.update_xaxes(rangeslider=dict(visible=True))
            fig.show()

    def unravel_vectors(self):
        """ Unravels vector and list entries in tub which are created
            when the DataFrame is created from a list of records"""
        for k, v in zip(self.parent.ids.tub_loader.tub.manifest.inputs,
                        self.parent.ids.tub_loader.tub.manifest.types):
            if v == 'vector' or v == 'list':
                dim = len(self.parent.current_record.underlying[k])
                df_keys = [k + f'_{i}' for i in range(dim)]
                self.df[df_keys] = pd.DataFrame(self.df[k].tolist(),
                                                index=self.df.index)
                self.df.drop(k, axis=1, inplace=True)

    def update_dataframe_from_tub(self):
        """ Called from TubManager when a tub is reloaded/recreated. Fills
            the DataFrame from records, and updates the dropdown menu in the
            data panel."""
        underlying_generator = (t.underlying for t in
                                self.parent.ids.tub_loader.records)
        self.df = pd.DataFrame(underlying_generator).dropna()
        to_drop = {'cam/image_array'}
        self.df.drop(labels=to_drop, axis=1, errors='ignore', inplace=True)
        self.df.set_index('_index', inplace=True)
        self.unravel_vectors()
        self.parent.ids.data_panel.ids.data_spinner.values = self.df.columns
        self.plot_from_current_bars()


class TubWindow(BoxLayout):
    index = NumericProperty(None, force_dispatch=True)
    current_record = ObjectProperty(None)

    def initialise(self):
        self.ids.config_manager.load_action()
        self.ids.tub_loader.update_tub()
        self.index = 0

    def on_index(self, obj, index):
        self.current_record = self.ids.tub_loader.records[index]
        self.ids.slider.value = index

    def on_current_record(self, obj, record):
        self.ids.img.update(record)
        i = record.underlying['_index']
        self.ids.control_panel.record_display = f"Record {i:06}"
        self.ids.data_panel.update(record)

    def status(self, msg):
        self.ids.status.text = msg


class TubApp(App):
    layout = None

    def __init__(self):
        super().__init__(title='Tub Manager')

    def build(self):
        self.layout = TubWindow()
        self.layout.initialise()
        return self.layout


if __name__ == '__main__':
    tub_app = TubApp()
    tub_app.run()
