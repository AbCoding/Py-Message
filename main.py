import FrequencyTableGenerator
import GenerateWordCloud
import MessageFileToDatabase
import MessagesOverTime


def GenMessages(filename, GroupBy="week", SeperateBySender=False, sender="all"):
    MessageFileToDatabase.WriteToCSV(filename+".html",filename+".csv")

    MessagesOverTime.GenerateTimeline(filename, GroupBy=GroupBy, SeperateBySender=SeperateBySender, sender=sender)
def MakeWordCloudFromScratch(filename,FilterDict=False,BlackListedWords=[]):
    MessageFileToDatabase.WriteToCSV(filename+".html",filename+".csv")
    GenerateWordCloud.GenerateWordCloud(filename,FilterDict,BlackListedwords=BlackListedWords)


