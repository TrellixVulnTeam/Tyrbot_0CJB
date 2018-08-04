from core.decorators import instance, command
from core.chat_blob import ChatBlob
from core.command_param_types import Any, Const, Int
from xml.etree import ElementTree
import os
import requests
import re
import bbcode

from core.dict_object import DictObject


@instance()
class AOUController:
    AOU_URL = "https://www.ao-universe.com/mobile/parser.php?bot=budabot"

    CACHE_GROUP = "aou"
    CACHE_MAX_AGE = 604800

    def __init__(self):
        self.item_regex = re.compile(r"\[(item|itemname|itemicon)( nolink)?\](\d+)\[\/(item|itemname|itemicon)\]", re.IGNORECASE)
        self.guide_id_regex = re.compile(r"pid=(\d+)", re.IGNORECASE)

        # initialize bbcode parser
        self.parser = bbcode.Parser(install_defaults=False, newline="\n", replace_links=False, replace_cosmetic=False, drop_unrecognized=True)
        self.parser.add_simple_formatter("i", "<i>%(value)s</i>")
        self.parser.add_simple_formatter("b", "<highlight>%(value)s<end>")
        self.parser.add_simple_formatter("ts_ts", " + ", standalone=True)
        self.parser.add_simple_formatter("ts_ts2", " = ", standalone=True)
        self.parser.add_simple_formatter("ct", " | ", standalone=True)
        self.parser.add_simple_formatter("cttd", " | ", standalone=True)
        self.parser.add_simple_formatter("cttr", "\n | ", standalone=True)
        self.parser.add_simple_formatter("br", "\n", standalone=True)
        self.parser.add_formatter("img", self.bbcode_render_image)
        self.parser.add_formatter("url", self.bbcode_render_url)
        self.parser.add_formatter("item", self.bbcode_render_item)
        self.parser.add_formatter("itemname", self.bbcode_render_item)
        self.parser.add_formatter("itemicon", self.bbcode_render_item)
        self.parser.add_formatter("waypoint", self.bbcode_render_waypoint)

    def inject(self, registry):
        self.text = registry.get_instance("text")
        self.items_controller = registry.get_instance("items_controller")
        self.cache_service = registry.get_instance("cache_service")

    @command(command="aou", params=[Int("guide_id")], access_level="all",
             description="Show an AO-Universe guide")
    def aou_show_cmd(self, channel, sender, reply, args):
        guide_id = args[0]

        guide_info = self.retrieve_guide(guide_id)

        if not guide_info:
            reply("Could not find AO-Universe guide with id <highlight>%d<end>." % guide_id)
            return

        blob = ""
        blob += "Id: " + self.text.make_chatcmd(guide_info.id, "/start https://www.ao-universe.com/main.php?site=knowledge&id=%s" % guide_info.id) + "\n"
        blob += "Updated: <highlight>%s<end>\n" % guide_info.update
        blob += "Profession: <highlight>%s<end>\n" % guide_info["class"]
        blob += "Faction: <highlight>%s<end>\n" % guide_info.faction
        blob += "Level: <highlight>%s<end>\n" % guide_info.level
        blob += "Author: <highlight>%s<end>\n\n" % self.format_bbcode_code(guide_info.author)
        blob += self.format_bbcode_code(guide_info.text)
        blob += "\n\n<highlight>Powered by<end> " + self.text.make_chatcmd("AO-Universe.com", "/start https://www.ao-universe.com")

        reply(ChatBlob(guide_info.name, blob))

    @command(command="aou", params=[Const("all", is_optional=True), Any("search")], access_level="all",
             description="Search for an AO-Universe guides")
    def aou_search_cmd(self, channel, sender, reply, args):
        include_all_matches = True if args[0] else False
        search = args[1]

        r = requests.get(self.AOU_URL + "&mode=search&search=" + search)
        xml = ElementTree.fromstring(r.content)

        blob = ""
        count = 0
        for section in xml.iter("section"):
            category = self.get_category(section)
            found = False
            for guide in self.get_guides(section):
                if include_all_matches or self.check_matches(category + " " + guide["name"] + " " + guide["description"], search):
                    # don't show category unless we have at least one guide for it
                    if not found:
                        blob += "\n<header2>%s<end>\n" % category
                        found = True

                    count += 1
                    blob += "%s - %s\n" % (self.text.make_chatcmd(guide["name"], "/tell <myname> aou %s" % guide["id"]), guide["description"])
        blob += "\n\nPowered by %s" % self.text.make_chatcmd("AO-Universe.com", "/start https://www.ao-universe.com")

        if count == 0:
            reply("Could not find any AO-Universe guides for search <highlight>%s<end>." % search)
        else:
            reply(ChatBlob("%sAOU Guides containing '%s' (%d)" % ("All " if include_all_matches else "", search, count), blob))

    def retrieve_guide(self, guide_id):
        cache_key = "%d.xml" % guide_id

        # check cache for fresh value
        cache_result = self.cache_service.retrieve(self.CACHE_GROUP, cache_key, self.CACHE_MAX_AGE)

        if cache_result:
            result = ElementTree.fromstring(cache_result)
        else:
            response = requests.get(self.AOU_URL + "&mode=view&id=" + str(guide_id))
            result = ElementTree.fromstring(response.content)

            if result.findall("./error"):
                result = None

            if result:
                # store result in cache
                self.cache_service.store(self.CACHE_GROUP, cache_key, ElementTree.tostring(result, encoding="unicode"))
            else:
                # check cache for any value, even expired
                cache_result = self.cache_service.retrieve(self.CACHE_GROUP, cache_key)
                if cache_result:
                    result = ElementTree.fromstring(cache_result)

        if result:
            return self.get_guide_info(result)
        else:
            return None

    def get_guide_info(self, xml):
        content = self.get_xml_child(xml, "section/content")
        return DictObject({
            "id": self.get_xml_child(content, "id").text,
            "category": self.get_category(self.get_xml_child(xml, "section")),
            "name": self.get_xml_child(content, "name").text,
            "update": self.get_xml_child(content, "update").text,
            "class": self.get_xml_child(content, "class").text,
            "faction": self.get_xml_child(content, "faction").text,
            "level": self.get_xml_child(content, "level").text,
            "author": self.get_xml_child(content, "author").text,
            "text": self.get_xml_child(content, "text").text
        })

    def check_matches(self, haystack, needle):
        haystack = haystack.lower()
        for n in needle.split():
            if n in haystack:
                return True
        return False

    def get_base_path(self):
        return os.path.dirname(os.path.realpath(__file__)) + os.sep + "guides"

    def get_guides(self, section):
        result = []
        for guide in section.findall("./guidelist/guide"):
            result.append({"id": guide[0].text, "name": guide[1].text, "description": guide[2].text})
        return result

    def get_category(self, section):
        result = []
        for folder_names in section.findall("./folderlist/folder/name"):
            result.append(folder_names.text)
        return " - ".join(reversed(result))

    def get_xml_child(self, xml, child_tag):
        return xml.findall("./%s" % child_tag)[0]

    def format_bbcode_code(self, bbcode_str):
        return self.parser.format(bbcode_str)

    # BBCode formatters
    def bbcode_render_image(self, tag_name, value, options, parent, context):
        return "-image-"

    def bbcode_render_url(self, tag_name, value, options, parent, context):
        url = options["url"]
        guide_id_match = self.guide_id_regex.search(url)
        if guide_id_match:
            return self.text.make_chatcmd(value, "/tell <myname> aou " + guide_id_match.group(1))
        else:
            return self.text.make_chatcmd(value, "/start " + url)

    def bbcode_render_item(self, tag_name, value, options, parent, context):
        item = self.items_controller.get_by_item_id(value)
        if not item:
            return "Unknown Item(%s)" % value
        else:
            include_icon = tag_name == "item" or tag_name == "itemicon"
            return self.text.format_item(item, with_icon=include_icon)

    def bbcode_render_waypoint(self, tag_name, value, options, parent, context):
        x_coord = options["x"]
        y_coord = options["y"]
        pf_id = options["pf"]

        return self.text.make_chatcmd("%s (%sx%s)" % (value, x_coord, y_coord), "/waypoint %s %s %s" % (x_coord, y_coord, pf_id))